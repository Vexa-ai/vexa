"use client";
/** MeetingShare — ONE Share action for a meeting, live and after, and the quiet "shared" indicator.
 *
 *  `MeetingShareButton` sits in the meeting header (the canvas tab and the minutes page header). For
 *  the OWNER it opens `MeetingShareDialog`; for someone the meeting was shared WITH it is not an
 *  action at all, just the words "Shared with you" — managing access is the owner's.
 *
 *  The dialog has four small parts, each one backend call away (`meetingShareApi`):
 *    • Invite people by email — one invite per address, mailed to them by the server, optionally with
 *      the meeting's workspace (view or edit) bundled into the same link; the link can also be copied;
 *    • People with access — readers, pending invites and workspace members, each with a role and a
 *      remove action;
 *    • A link — anyone who opens it must sign in; it can be turned off, which removes whoever came
 *      in through it;
 *    • The recording — whether the people you share with may play and download it.
 *  Every failure is shown where it happened, in words (`presentError`), never swallowed (P18). */
import { useCallback, useEffect, useState, type CSSProperties, type ReactNode } from "react";
import { Icon } from "../ui-kit";
import { Modal } from "../ui-kit/Modal";
import { copyText } from "../ui-kit/ContextMenu";
import { presentError } from "./apiClient";
import { listWorkspaceMembers, removeWorkspaceMember, type WorkspaceMember } from "./workspaceApi";
import {
  createShareLink, getMeetingAccess, inviteToMeeting, parseEmails, removeReader, revokeShare, setRecordingShared,
  type InviteResult, type MeetingAccess, type WorkspaceGrant,
} from "./meetingShareApi";
import type { MeetingMock } from "./meetingModel";

const quiet: CSSProperties = { fontSize: 12, color: "var(--t3)" };
const field: CSSProperties = { fontSize: 12.5, padding: "5px 8px", background: "var(--bg)", border: "1px solid var(--line)", borderRadius: 6, color: "var(--t1)", minWidth: 0 };
const textBtn: CSSProperties = { background: "transparent", border: "none", padding: 0, color: "var(--t2)", fontSize: 12, cursor: "pointer", textDecoration: "underline dotted", textUnderlineOffset: 3 };
const primary: CSSProperties = { fontSize: 12.5, padding: "5px 12px", background: "var(--accent)", color: "var(--on-accent, var(--bg))", border: "none", borderRadius: 6, cursor: "pointer" };
const headerBtn: CSSProperties = { display: "inline-flex", alignItems: "center", gap: 5, background: "transparent", border: "1px solid var(--line2)", color: "var(--t2)", borderRadius: 6, padding: "2px 9px", fontSize: 12, cursor: "pointer", flex: "none" };

function Section({ title, children }: { title: string; children: ReactNode }) {
  return <section style={{ display: "flex", flexDirection: "column", gap: 8, paddingTop: 12, marginTop: 12, borderTop: "1px solid var(--line)" }}>
    <div style={{ fontSize: 11, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".05em" }}>{title}</div>
    {children}
  </section>;
}

function Alert({ children }: { children: ReactNode }) {
  return <div role="alert" style={{ fontSize: 12, color: "var(--danger)", lineHeight: 1.4 }}>{children}</div>;
}

/** One row of the access list: who, what they can do, and the remove action. */
function AccessRow({ who, role, onRemove, busy }: { who: string; role: string; onRemove?: () => void; busy?: boolean }) {
  return <div style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 12.5 }}>
    <Icon name="user" size={13} style={{ color: "var(--t3)" }} />
    <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: "var(--t1)" }}>{who}</span>
    <span style={quiet}>{role}</span>
    {onRemove && <button type="button" style={textBtn} disabled={busy} onClick={onRemove}>Remove</button>}
  </div>;
}

/** The quiet indicator: an icon and a few words — never a chip. */
export function SharedIndicator({ meeting }: { meeting: Pick<MeetingMock, "shared" | "shared_with"> }) {
  if (meeting.shared) return <span title="Someone shared this meeting with you" style={{ ...quiet, display: "inline-flex", alignItems: "center", gap: 4, flex: "none" }}>
    <Icon name="user" size={11} />Shared with you</span>;
  const n = meeting.shared_with ?? 0;
  if (!n) return null;
  return <span title={`${n} ${n === 1 ? "person" : "people"} can read this meeting`} style={{ ...quiet, display: "inline-flex", alignItems: "center", gap: 4, flex: "none" }}>
    <Icon name="user" size={11} />Shared with {n}</span>;
}

/** The header action. Owners get the dialog; recipients get the indicator and nothing to press. */
export function MeetingShareButton({ meeting }: { meeting: MeetingMock }) {
  const [open, setOpen] = useState(false);
  if (meeting.shared) return <SharedIndicator meeting={meeting} />;
  return <>
    <button type="button" onClick={() => setOpen(true)} style={headerBtn} title="Share this meeting">
      <Icon name="link" size={12} /> Share{meeting.shared_with ? ` · ${meeting.shared_with}` : ""}
    </button>
    {open && <MeetingShareDialog meeting={meeting} onClose={() => setOpen(false)} />}
  </>;
}

export function MeetingShareDialog({ meeting, onClose, origin }: { meeting: MeetingMock; onClose: () => void; origin?: string }) {
  const base = origin ?? (typeof window !== "undefined" ? window.location.origin : "");
  const [access, setAccess] = useState<MeetingAccess | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [members, setMembers] = useState<WorkspaceMember[] | null>(null);
  const [membersError, setMembersError] = useState<string | null>(null);
  const [emails, setEmails] = useState("");
  const [wsRole, setWsRole] = useState<WorkspaceGrant>("none");
  const [results, setResults] = useState<InviteResult[]>([]);
  const [inputError, setInputError] = useState<string | null>(null);
  const [link, setLink] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const workspaceId = access?.workspace_id ?? meeting.workspace_id ?? null;

  const load = useCallback(async () => {
    setLoadError(null);
    try { setAccess(await getMeetingAccess(meeting.id)); }
    catch (e) { setLoadError(presentError(e).headline); }
  }, [meeting.id]);
  useEffect(() => { void load(); }, [load]);

  useEffect(() => {
    if (!workspaceId) { setMembers(null); return; }
    setMembersError(null);
    listWorkspaceMembers(workspaceId).then(setMembers).catch((e) => setMembersError(presentError(e).headline));
  }, [workspaceId]);

  /** Run one owner action; a failure is shown in the dialog, the list is replaced on success. */
  const act = async (fn: () => Promise<MeetingAccess | void>) => {
    setBusy(true); setActionError(null);
    try { const next = await fn(); if (next) setAccess(next); }
    catch (e) { setActionError(presentError(e).headline); }
    finally { setBusy(false); }
  };

  const invite = async () => {
    const { emails: list, invalid } = parseEmails(emails);
    if (invalid.length) { setInputError(`Not an email address: ${invalid.join(", ")}`); return; }
    if (!list.length) { setInputError("Type at least one email address."); return; }
    setInputError(null); setBusy(true); setActionError(null);
    try {
      const r = await inviteToMeeting({ meetingId: meeting.id, emails: list, workspaceId, workspaceRole: wsRole, origin: base });
      setResults(r);
      if (r.some((x) => x.url)) setEmails(r.filter((x) => x.error).map((x) => x.email).join(", "));
      await load();
    } finally { setBusy(false); }
  };

  const openLink = access?.links[0];
  const readers = access?.people ?? [];
  const pending = access?.invites ?? [];

  return <Modal title="Share meeting" onClose={onClose} width={480}>
    <div style={{ fontSize: 12.5, color: "var(--t2)", lineHeight: 1.5 }}>
      People you share with see the live transcript while the meeting runs, and the transcript, notes and — if you allow it — the recording after. Everyone signs in first.
    </div>

    <Section title="Invite people">
      <div style={{ display: "flex", gap: 6 }}>
        <input aria-label="Email addresses" value={emails} placeholder="name@company.com, …" disabled={busy}
          onChange={(e) => { setEmails(e.target.value); setInputError(null); }}
          onKeyDown={(e) => { if (e.key === "Enter") void invite(); }} style={{ ...field, flex: 1 }} />
        <button type="button" style={{ ...primary, opacity: busy ? 0.5 : 1 }} disabled={busy} onClick={() => void invite()}>{busy ? "Inviting…" : "Invite"}</button>
      </div>
      {workspaceId && <label style={{ display: "flex", alignItems: "center", gap: 8, ...quiet }}>
        Also the workspace <span style={{ fontFamily: "var(--mono)", color: "var(--t2)" }}>{workspaceId}</span>
        <select aria-label="Workspace access" value={wsRole} disabled={busy} onChange={(e) => setWsRole(e.target.value as WorkspaceGrant)} style={field}>
          <option value="none">no</option>
          <option value="viewer">can view</option>
          <option value="contributor">can edit</option>
        </select>
      </label>}
      {inputError && <Alert>{inputError}</Alert>}
      {results.map((r) => r.url
        ? <div key={r.email} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12 }}>
            <span style={{ flex: 1, minWidth: 0, color: "var(--t2)" }}>{r.mailed
              ? <>Emailed <b style={{ color: "var(--t1)" }}>{r.email}</b> their link. You can also copy it — it works only for that address.</>
              : <>The email to <b style={{ color: "var(--t1)" }}>{r.email}</b> could not be sent. Copy the link and send it yourself — it works only for that address.</>}</span>
            <button type="button" style={textBtn} onClick={() => void copyText(r.url!)}>Copy link</button>
          </div>
        : <Alert key={r.email}>Could not invite {r.email}: {presentError(r.error).headline}</Alert>)}
    </Section>

    <Section title="People with access">
      {loadError && <Alert>Could not load who has access: {loadError} <button type="button" style={textBtn} onClick={() => void load()}>Retry</button></Alert>}
      {!access && !loadError && <span role="status" style={quiet}>Loading…</span>}
      {access && !readers.length && !pending.length && !(members && members.length) && <span style={quiet}>Only you.</span>}
      {readers.map((p) => <AccessRow key={`r${p.user_id}`} who={p.email ?? `User ${p.user_id}`} role="can view" busy={busy}
        onRemove={() => void act(() => removeReader(meeting.id, p.user_id))} />)}
      {pending.map((i) => <AccessRow key={`i${i.id}`} who={i.emails.join(", ")} role="invited, not opened yet" busy={busy}
        onRemove={() => void act(() => revokeShare(meeting.id, i.id))} />)}
      {workspaceId && <>
        {membersError && <Alert>Could not load the workspace members: {membersError}</Alert>}
        {members?.map((m) => <AccessRow key={`w${m.subject}`} who={m.email || m.name || m.subject}
          role={m.role === "owner" ? "workspace owner" : m.role === "viewer" ? "workspace · can view" : "workspace · can edit"} busy={busy}
          onRemove={m.role === "owner" ? undefined : () => void act(async () => {
            await removeWorkspaceMember(workspaceId, m.subject);
            setMembers(await listWorkspaceMembers(workspaceId));
          })} />)}
      </>}
    </Section>

    <Section title="Link">
      {openLink
        ? <div style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 12.5 }}>
            <span style={{ flex: 1, color: "var(--t2)" }}>Anyone with the link can view after signing in{openLink.joined ? ` · ${openLink.joined} joined` : ""}.</span>
            <button type="button" style={textBtn} disabled={busy} onClick={() => void act(async () => { setLink(null); return revokeShare(meeting.id, openLink.id); })}>Turn off</button>
          </div>
        : <span style={quiet}>Off. Only the people above can open this meeting.</span>}
      {link
        ? <div style={{ display: "flex", gap: 6 }}>
            <input readOnly aria-label="Share link" value={link} onFocus={(e) => e.currentTarget.select()} style={{ ...field, flex: 1, fontSize: 11.5 }} />
            <button type="button" style={primary} onClick={() => void copyText(link)}>Copy</button>
          </div>
        : <div><button type="button" style={textBtn} disabled={busy} onClick={() => void act(async () => {
            setLink(await createShareLink(meeting.id, base));
            return getMeetingAccess(meeting.id);
          })}>{openLink ? "Create another link" : "Create a link"}</button></div>}
    </Section>

    <Section title="Recording">
      <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12.5, color: "var(--t2)" }}>
        <input type="checkbox" checked={!!access?.recording} disabled={!access || busy}
          onChange={(e) => { const on = e.target.checked; void act(() => setRecordingShared(meeting.id, on)); }} />
        People you share with can play and download the recording
      </label>
    </Section>

    {actionError && <div style={{ marginTop: 10 }}><Alert>{actionError}</Alert></div>}
  </Modal>;
}
