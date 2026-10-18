"use client";
/** MentionUI — the composer's @ picker, its mention chips, and the one question Enter asks before a
 *  message shares a chat (ADR-0048).
 *
 *  The question is asked once, inline, above the composer, and says what the owner is choosing:
 *  who joins, that a private chat is shared with what was said in it (or, the second answer, a new
 *  chat starts with them), what its history read that only the owner may, what the newcomers do not see, and — for a routine's chat — which of
 *  the owner's private reads its runs would lose. Three answers: share and send, send without
 *  sharing (the names go as plain text; nobody is told), or cancel (nothing is sent). */
import { accountsLabel, joinNames, type Mention, type PickerRow } from "./mentions";
import type { SharePreview } from "./sessionsApi";
import { Identity } from "../ui-kit/Identity";
import { personPhotoUrl } from "../platform/identity";
import "./mentionUI.css";

export function MentionPicker({ rows, active, onPick }: { rows: PickerRow[]; active: number; onPick: (row: PickerRow) => void }) {
  if (!rows.length) return null;
  return (
    <div role="listbox" aria-label="People you can mention" className="vx-mention-box">
      {rows.map((r, i) => (
        <div key={r.kind === "agent" ? `a:${r.name}` : `p:${r.person.key ?? r.person.subject}`} role="option" aria-selected={i === active}
          onMouseDown={(e) => { e.preventDefault(); onPick(r); }} className="vx-mention-row">
          {r.kind === "agent"
            ? <Identity kind="agent" id={r.name} name={r.name} size={20} />
            : <Identity kind="person" id={r.person.key ?? r.person.subject} name={r.person.name || r.label} size={20}
                user={!!r.person.vexa} src={r.person.vexa && r.person.subject ? personPhotoUrl(r.person.subject) : undefined} />}
          <span className="vx-mention-handle">@{r.label}</span>
          <span className="vx-mention-says">
            {r.kind === "agent" ? "the agent — answers this message" : (r.person.name && r.person.name !== r.label ? `${r.person.name} · ` : "") + (r.person.email || "")}
          </span>
          {r.kind === "person" && accountsLabel(r.person) && (
            <span className="vx-mention-accounts" data-accounts={r.person.accounts}
              title={accountsLabel(r.person).endsWith("?") ? "These sign-ins may be one person; until they confirm, adding them adds this one only" : "One person, several sign-ins: adding them adds every one"}>{accountsLabel(r.person)}</span>
          )}
        </div>
      ))}
    </div>
  );
}

export function MentionChips({ mentions, onRemove }: { mentions: Mention[]; onRemove: (m: Mention) => void }) {
  if (!mentions.length) return null;
  return (
    <div className="vx-mention-chips">
      {mentions.map((m) => (
        <span key={m.subject} title={m.name} className="vx-mention-chip">
          <Identity kind="person" id={m.subject} name={m.name || m.label} size={14} user src={personPhotoUrl(m.subject)} />
          @{m.label}
          <button aria-label={`Remove mention of ${m.label}`} onClick={() => onRemove(m)} className="vx-mention-chip-x">×</button>
        </span>
      ))}
    </div>
  );
}

/** The sentences the confirmation says. Pure, so a test reads exactly what a person reads. */
export function shareLines(people: Mention[], owner: boolean, preview: SharePreview | null): { title: string; lines: string[] } {
  const who = joinNames(people.map((p) => p.name || p.label));
  if (!owner) {
    return { title: `${who} ${people.length === 1 ? "is" : "are"} not in this chat`,
      lines: ["Only the chat's owner can add people. Send without sharing and the name goes as plain text; nobody is told."] };
  }
  const lines: string[] = [];
  const one = people.length === 1;
  if (preview?.forks !== false) {
    lines.push(`${who} ${one ? "joins" : "join"} this chat and ${one ? "sees" : "see"} what was said here, the agent's answers included.`);
    const reads = preview?.private_reads ?? [];
    if (reads.length && !preview?.routine) {
      lines.push(`Its history includes your ${joinNames(reads.map((r) => r.replace(/^your /, "")))} reads.`);
    }
    lines.push(`Or start a new chat with ${one ? "them" : "them all"} that begins with this message; this one stays yours.`);
  } else {
    lines.push(`${who} ${one ? "joins" : "join"} this chat and ${one ? "sees" : "see"} it, as everyone here does.`);
  }
  lines.push("People you add see this conversation, not your workspaces. With others here, the agent answers when mentioned (@vexa) and uses only what everyone can read.");
  if (preview?.routine) {
    const reads = preview.private_reads ?? [];
    if (reads.length) {
      lines.push(`This routine reads your ${joinNames(reads.map((r) => r.replace(/^your /, "")))}. Shared, it can't — its runs would only use what everyone here can see.`);
    }
    lines.push("The routine stays yours: only you pause, edit or delete it. Everyone here sees its runs and can reply to steer the next one.");
  }
  return { title: `Share this chat with ${who}?`, lines };
}

export function ShareConfirm({ people, owner, preview, busy, error, onShare, onSendPlain, onCancel }: {
  people: Mention[]; owner: boolean; preview: SharePreview | null; busy: boolean; error?: string;
  onShare: (history: "all" | "none" | null) => void; onSendPlain: () => void; onCancel: () => void;
}) {
  const forks = owner && preview?.forks !== false;
  const { title, lines } = shareLines(people, owner, preview);
  const warns = !!preview?.routine && (preview.private_reads ?? []).length > 0;
  return (
    <div role="dialog" aria-label={title} className="vx-mention-box vx-share">
      <div className="vx-share-title">{title}</div>
      {lines.map((l) => <div key={l} className="vx-share-line">{l}</div>)}
      {error && <div role="alert" className="vx-share-error">{error}</div>}
      <div className="vx-share-acts">
        {owner && (
          <button disabled={busy} onClick={() => onShare(forks ? "all" : null)} className="vx-share-act" data-tone="primary">
            {warns ? "Share anyway" : "Share and send"}
          </button>
        )}
        {forks && (
          <button disabled={busy} onClick={() => onShare("none")} className="vx-share-act">New chat with them</button>
        )}
        <button disabled={busy} onClick={onSendPlain} className="vx-share-act">Send without sharing</button>
        <button disabled={busy} onClick={onCancel} className="vx-share-act" data-tone="quiet">Cancel</button>
      </div>
    </div>
  );
}

/** WHAT AN @-PICK IS FOR (founder 2026-10-15: "to tag and to invite to a call"): a small inline
 *  choice after the chip, a tag unless the person picks otherwise. Inviting into the chat is for a
 *  Vexa account (and asks before sharing); a call rings a Vexa account, and for anyone else the
 *  agent drafts the invitation with the call's link for the person to send. */
export function MentionChoice({ name, vexa, onChoose }: { name: string; vexa: boolean; onChoose: (i: "tag" | "chat" | "call") => void }) {
  return (
    <div role="group" aria-label={`What to do with ${name}`} className="vx-mention-choice" data-mention-choice="">
      <button type="button" data-choice="tag" className="vx-mention-choice-btn" onClick={() => onChoose("tag")}>Tag</button>
      {vexa && <button type="button" data-choice="chat" className="vx-mention-choice-btn" onClick={() => onChoose("chat")}>Invite to this chat</button>}
      <button type="button" data-choice="call" className="vx-mention-choice-btn" onClick={() => onChoose("call")}>Invite to a call</button>
    </div>
  );
}
