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
import "./meetingShare.css";
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Icon } from "../ui-kit";
import { Modal } from "../ui-kit/Modal";
import { copyText } from "../ui-kit/ContextMenu";
import { presentError } from "../data/apiClient";
import { listWorkspaceMembers, removeWorkspaceMember, type WorkspaceMember } from "../data/workspaceApi";
import {
  createShareLink, getMeetingAccess, inviteToMeeting, parseEmails, removeReader, revokeShare, setRecordingShared,
  type InviteResult, type MeetingAccess, type WorkspaceGrant,
} from "./meetingShareApi";
import type { MeetingMock } from "./meetingModel";

// The look lives in ./meetingShare.css (tokens only).
const quiet = "vx-share-quiet";
const field = "vx-share-field";
const textBtn = "vx-share-text-btn";
const primary = "vx-share-primary";
const headerBtn = "vx-share-header-btn";

function Section({ title, children }: { title: string; children: ReactNode }) {
  return <section className="vx-share-section">
    <div className="vx-share-section-title">{title}</div>
    {children}
  </section>;
}

function Alert({ children }: { children: ReactNode }) {
  return <div role="alert" className="vx-share-alert">{children}</div>;
}

/** One row of the access list: who, what they can do, and the remove action. */
function AccessRow({ who, role, onRemove, busy }: { who: string; role: string; onRemove?: () => void; busy?: boolean }) {
  return <div className="vx-share-access-row">
    <Icon name="user" size={13} />
    <span className="vx-share-who">{who}</span>
    <span className={quiet}>{role}</span>
    {onRemove && <button type="button" className={textBtn} disabled={busy} onClick={onRemove}>Remove</button>}
  </div>;
}

/** The quiet indicator: an icon and a few words — never a chip. */
export function SharedIndicator({ meeting }: { meeting: Pick<MeetingMock, "shared" | "shared_with"> }) {
  if (meeting.shared) return <span title="Someone shared this meeting with you" className={`${quiet} vx-share-indicator`}>
    <Icon name="user" size={11} />Shared with you</span>;
  const n = meeting.shared_with ?? 0;
  if (!n) return null;
  return <span title={`${n} ${n === 1 ? "person" : "people"} can read this meeting`} className={`${quiet} vx-share-indicator`}>
    <Icon name="user" size={11} />Shared with {n}</span>;
}

/** The header action. Owners get the dialog; recipients get the indicator and nothing to press. */
export function MeetingShareButton({ meeting }: { meeting: MeetingMock }) {
  const [open, setOpen] = useState(false);
  if (meeting.shared) return <SharedIndicator meeting={meeting} />;
  return <>
    <button type="button" onClick={() => setOpen(true)} className={headerBtn} title="Share this meeting">
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
    <div className="vx-share-lede">
      People you share with see the live transcript while the meeting runs, and the transcript, notes and — if you allow it — the recording after. Everyone signs in first.
    </div>

    <Section title="Invite people">
      <div className="vx-share-inline">
        <input aria-label="Email addresses" value={emails} placeholder="name@company.com, …" disabled={busy}
          onChange={(e) => { setEmails(e.target.value); setInputError(null); }}
          onKeyDown={(e) => { if (e.key === "Enter") void invite(); }} className={field} style={{ flex: 1 }} />
        <button type="button" className={primary} style={{ opacity: busy ? 0.5 : 1 }} disabled={busy} onClick={() => void invite()}>{busy ? "Inviting…" : "Invite"}</button>
      </div>
      {workspaceId && <label className={`${quiet} vx-share-ws`}>
        Also the workspace <span className="vx-share-ws-id">{workspaceId}</span>
        <select aria-label="Workspace access" value={wsRole} disabled={busy} onChange={(e) => setWsRole(e.target.value as WorkspaceGrant)} className={field}>
          <option value="none">no</option>
          <option value="viewer">can view</option>
          <option value="contributor">can edit</option>
        </select>
      </label>}
      {inputError && <Alert>{inputError}</Alert>}
      {results.map((r) => r.url
        ? <div key={r.email} className="vx-share-result">
            <span className="vx-share-result-text">{r.mailed
              ? (r.workspace
                ? <>Emailed <b className="c-1">{r.email}</b> the meeting. Their workspace invite is only in the copied link — send them that too. It works only for that address.</>
                : <>Emailed <b className="c-1">{r.email}</b> their link. You can also copy it — it works only for that address.</>)
              : <>The email to <b className="c-1">{r.email}</b> could not be sent. Copy the link and send it yourself — it works only for that address.</>}</span>
            <button type="button" className={textBtn} onClick={() => void copyText(r.url!)}>Copy link</button>
          </div>
        : <Alert key={r.email}>Could not invite {r.email}: {presentError(r.error).headline}</Alert>)}
    </Section>

    <Section title="People with access">
      {loadError && <Alert>Could not load who has access: {loadError} <button type="button" className={textBtn} onClick={() => void load()}>Retry</button></Alert>}
      {!access && !loadError && <span role="status" className={quiet}>Loading…</span>}
      {access && !readers.length && !pending.length && !(members && members.length) && <span className={quiet}>Only you.</span>}
      {readers.map((p) => <AccessRow key={`r${p.user_id}`} who={p.email ?? `User ${p.user_id}`} role="can view" busy={busy}
        onRemove={() => void act(() => removeReader(meeting.id, p.user_id))} />)}
      {pending.map((i) => <AccessRow key={`i${i.id}`} who={i.emails.join(", ")} role="invited, not opened yet" busy={busy}
        onRemove={() => void act(async () => {
          // ONE invite row can stand for several grants (the copied link and the emailed one);
          // withdrawing the invite withdraws every one of them.
          let last: MeetingAccess | undefined;
          for (const id of (i.ids?.length ? i.ids : [i.id])) last = await revokeShare(meeting.id, id);
          return last;
        })} />)}
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
        ? <div className="vx-share-link-row">
            <span className="vx-share-link-text">Anyone with the link can view after signing in{openLink.joined ? ` · ${openLink.joined} joined` : ""}.</span>
            <button type="button" className={textBtn} disabled={busy} onClick={() => void act(async () => { setLink(null); return revokeShare(meeting.id, openLink.id); })}>Turn off</button>
          </div>
        : <span className={quiet}>Off. Only the people above can open this meeting.</span>}
      {link
        ? <div className="vx-share-inline">
            <input readOnly aria-label="Share link" value={link} onFocus={(e) => e.currentTarget.select()} className={`${field} vx-share-link-field`} style={{ flex: 1 }} />
            <button type="button" className={primary} onClick={() => void copyText(link)}>Copy</button>
          </div>
        : <div><button type="button" className={textBtn} disabled={busy} onClick={() => void act(async () => {
            setLink(await createShareLink(meeting.id, base));
            return getMeetingAccess(meeting.id);
          })}>{openLink ? "Create another link" : "Create a link"}</button></div>}
    </Section>

    <Section title="Recording">
      <label className="vx-share-check">
        <input type="checkbox" checked={!!access?.recording} disabled={!access || busy}
          onChange={(e) => { const on = e.target.checked; void act(() => setRecordingShared(meeting.id, on)); }} />
        People you share with can play and download the recording
      </label>
    </Section>

    {actionError && <div className="vx-share-action-error"><Alert>{actionError}</Alert></div>}
  </Modal>;
}
