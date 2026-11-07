"use client";
/** THE PERSON PANEL (founder 2026-10-17; feature `shell2`): the right panel (a sheet on a phone),
 *  opened from any person row, chip, face or mention.
 *
 *  It leads with what the person wrote about themselves (S3's claimed profile: photo, name, headline,
 *  company, bio, location and local time, links as icons), then what sharing a workspace allows —
 *  the workspaces you share and their role there, rooms you are both in (S4), recent meetings
 *  together, cards between you (S5) — then "Notes from <org>": CRM fields only as your CRM access
 *  returns them, each marked when only you see it. Message opens the room of two once S4 lands;
 *  until then it says so. Someone not on Vexa reads "Not on Vexa yet" with Invite. Your own panel is
 *  your profile, with Edit and the "Public profile" switch (the existing public-profile settings). */
import { CopyRefButton } from "../readme/ObjectActions";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Clock, Github, Globe, Linkedin, MapPin, MessageSquare, Phone, Twitter, UserPlus } from "lucide-react";
import { Button, EmptyState, ExternalLink, Identity, ListRow, SectionLabel, formatDate } from "../../ui-kit";
import { useFeature } from "../../platform/features";
import { ASK_CHAT_EVENT, NEW_CHAT_AT_EVENT, OPEN_MEETING_EVENT, OPEN_SESSION_EVENT } from "../../platform/events";
import { useLiveMeetings } from "../../surfaces/liveMeetings";
import { PublicProfileSettings } from "../../surfaces/profile/PublicProfile";
import { openWorkspaceView } from "../desktop/home2/workspaceView";
import { WorkspaceMark } from "../rooms/WorkspaceMark";
import { callLabel } from "../callsList";
import { personPhotoUrl } from "../../platform/identity";
import { meNow } from "./me";
import { useWorkspaceNames } from "./names";
import { companyOf, presenceWord, type Person } from "./people";
import { photoOf, usePeople } from "./PeoplePage";
import { ME_KEY, keyOfPath } from "./personOpen";
import { localTime, readPersonProfile, type PersonProfile } from "./personProfile";
import { peopleOf } from "./meetings";
import { openRoomOfTwo, useRoomsOfTwo } from "./messageRoom";
import { holdPanel } from "./about";
import "./shell2.css";

const fire = (name: string, detail: unknown) => window.dispatchEvent(new CustomEvent(name, { detail }));
const LINK_ICON = { linkedin: Linkedin, github: Github, x: Twitter, website: Globe } as const;
const LINK_NAME = { linkedin: "LinkedIn", github: "GitHub", x: "X", website: "Website" } as const;

/** Find the person a panel address names: by key, then account, address or name. */
/** A person's full reference: their address, else their account id — never their bare name. */
export function personRef(p: { email?: string; subject?: string | null; name?: string }): string {
  // one format, no name inside (resolved when the badge is drawn)
  const id = p.email || p.subject || "";
  return id ? `[[person:${id}]]` : String(p.name ?? "").trim();
}

export function findPerson(people: Person[], key: string): Person | null {
  const k = key.trim();
  return people.find((p) => p.key === k || (p.aliases ?? []).includes(k))
    ?? (k.startsWith("u:") ? people.find((p) => p.subject === k.slice(2)) : undefined)
    ?? (k.startsWith("e:") ? people.find((p) => p.email === k.slice(2)) : undefined)
    ?? (k.startsWith("n:") ? people.find((p) => p.name.toLowerCase() === k.slice(2).toLowerCase()) : undefined)
    ?? null;
}

function Header({ name, photo, subject, lines, presence, actions }: { name: string; photo?: string; subject?: string | null; lines: string[]; presence?: string; actions?: ReactNode }) {
  return (
    <header className="vx-s2-pp-head">
      <Identity kind="person" id={subject || name} name={name} size={56} user={!!photo} src={photo} />
      <div className="vx-s2-pp-id">
        <h1>{name}</h1>
        {lines.filter(Boolean).map((l, i) => <p key={i} className="vx-s2-pp-line">{l}</p>)}
        {presence && <p className="vx-s2-pp-presence" data-presence={presence}>{presence}</p>}
      </div>
      {actions && <div className="vx-s2-pp-acts">{actions}</div>}
    </header>
  );
}

function MyProfile() {
  const me = meNow();
  const profilesOn = useFeature("public_profiles");
  const [editing, setEditing] = useState(false);
  return (
    <article className="vx-s2-pp" data-person-panel="me">
      <Header name={me.name || "You"} photo={me.subject ? personPhotoUrl(me.subject) : undefined} subject={me.subject} lines={[me.email]}
        actions={profilesOn ? <Button variant="secondary" onClick={() => setEditing((v) => !v)}>{editing ? "Done" : "Edit"}</Button> : undefined} />
      {profilesOn
        ? (editing ? <PublicProfileSettings /> : <p className="vx-s2-note">Your profile is what you write about yourself. People who share a workspace with you see it; nothing is public until you switch Public profile on (Edit).</p>)
        : <p className="vx-s2-note">Profiles are not switched on here.</p>}
    </article>
  );
}

export function PersonPanel({ path }: { path: string }) {
  const key = keyOfPath(path);
  const { phase, people } = usePeople();
  const wsName = useWorkspaceNames();
  const meetings = useLiveMeetings();
  const p = key === ME_KEY ? null : findPerson(people, key);
  const [profile, setProfile] = useState<PersonProfile | null>(null);
  // S4's room of two: Message is live once the server serves it (feature-detected, no side effect)
  const roomsOfTwo = useRoomsOfTwo();
  const [msgError, setMsgError] = useState("");
  useEffect(() => { setProfile(null); if (p?.subject) void readPersonProfile(p.subject).then(setProfile); }, [p?.subject]);
  const together = useMemo(() => !p ? [] : meetings.filter((m) => {
    const emails = (m.attendees ?? []).map((a) => a.email?.toLowerCase());
    return (p.email && emails.includes(p.email)) || peopleOf(m).some((n) => n.toLowerCase() === p.name.toLowerCase());
  }).sort((a, b) => Date.parse(b.start_time ?? b.scheduled_at ?? "") - Date.parse(a.start_time ?? a.scheduled_at ?? "")).slice(0, 5), [meetings, p]);
  if (key === ME_KEY) return <MyProfile />;
  if (!p) {
    const name = key.replace(/^[nue]:/, "");
    return phase === "ready"
      ? <article className="vx-s2-pp" data-person-panel="unknown"><Header name={name} lines={["Not on Vexa yet"]} /><p className="vx-s2-note">You share no workspace with them, and they are not among the contacts you can see.</p></article>
      : <EmptyState>Loading…</EmptyState>;
  }
  const company = profile?.company || companyOf(p.email);
  const onVexa = p.vexa || profile?.claimed;
  const tz = localTime(profile?.timezone);
  // the person by their FULL reference (founder 2026-10-18): a badge to the reader, an id to the agent
  const who = personRef(p);
  const call = () => { fire(NEW_CHAT_AT_EVENT, { workspace: "" }); window.setTimeout(() => fire(ASK_CHAT_EVENT, { prompt: `Call ${who}${p.email ? ` (${p.email})` : ""}: ring them in Vexa, or draft the call link for me to send.`, display: `Call ${who}` }), 150); };
  const invite = () => { fire(NEW_CHAT_AT_EVENT, { workspace: "" }); window.setTimeout(() => fire(ASK_CHAT_EVENT, { mode: "draft", prompt: `${who} Draft an invitation to Vexa for them${p.email ? ` <${p.email}>` : ""} — I'll review it before anything is sent.` }), 150); };
  const copyTarget = { kind: "person", id: p.email || p.subject || "", label: p.name };
  return (
    <article className="vx-s2-pp" data-person-panel={p.key}>
      <Header name={p.name} photo={profile?.photo || photoOf(p)} subject={p.subject}
        lines={[[profile?.headline || p.headline, company].filter(Boolean).join(" · "), onVexa ? "" : "Not on Vexa yet"]}
        presence={presenceWord(p.presence) || undefined}
        actions={onVexa ? <>
          {/* the room of two is S4's; until the server serves it, Message says so instead of failing */}
          {roomsOfTwo && p.subject
            ? <Button variant="secondary" icon={<MessageSquare size={14} />} data-message-person="" onClick={() => {
                setMsgError("");
                void openRoomOfTwo(p.subject!).then((sid) => { holdPanel(); fire(OPEN_SESSION_EVENT, { session: sid }); }).catch((e: Error) => setMsgError(e.message));
              }}>Message</Button>
            : <Button variant="secondary" icon={<MessageSquare size={14} />} disabled title="Coming with rooms of two">Message · coming</Button>}
          <Button variant="secondary" icon={<Phone size={14} />} onClick={call}>Start call</Button>
          {copyTarget.id && <CopyRefButton target={copyTarget} />}
        </> : <><Button variant="secondary" icon={<UserPlus size={14} />} onClick={invite}>Invite</Button>
          {copyTarget.id && <CopyRefButton target={copyTarget} />}</>} />
      {msgError && <p className="vx-s2-note" role="alert">{msgError}</p>}
      {(profile?.bio || profile?.location || tz || (profile?.links?.length ?? 0) > 0) && <section className="vx-s2-pp-about" data-profile="">
        {profile?.bio && <p className="vx-s2-pp-bio">{profile.bio}</p>}
        {(profile?.location || tz) && <p className="vx-s2-pp-line">{profile?.location && <><MapPin size={12} aria-hidden /> {profile.location}</>}{tz && <> <Clock size={12} aria-hidden /> {tz} their time</>}</p>}
        {(profile?.links?.length ?? 0) > 0 && <div className="vx-s2-pp-links">{profile!.links!.map((l) => { const I = LINK_ICON[l.kind]; return <ExternalLink key={l.url} href={l.url} aria-label={LINK_NAME[l.kind]} title={LINK_NAME[l.kind]}><I size={16} strokeWidth={1.75} aria-hidden /></ExternalLink>; })}</div>}
      </section>}
      {p.workspaces.length > 0 && <section className="vx-s2-sec" data-pp-sec="workspaces">
        <SectionLabel as="h3">Workspaces you share · {p.workspaces.length}</SectionLabel>
        {p.workspaces.map((w) => <ListRow key={w} leading={<WorkspaceMark id={w} name={wsName(w)} size={16} />} title={wsName(w)} meta={p.roles[w]}
          onSelect={() => void openWorkspaceView({ slug: w, name: wsName(w) })} />)}
      </section>}
      {together.length > 0 && <section className="vx-s2-sec" data-pp-sec="meetings">
        <SectionLabel as="h3">Meetings together</SectionLabel>
        {together.map((m) => <ListRow key={m.id} title={callLabel(m)} meta={m.start_time ? formatDate(m.start_time) : m.scheduled_at ? formatDate(m.scheduled_at) : undefined}
          onSelect={() => fire(OPEN_MEETING_EVENT, { ref: String(m.id) })} />)}
      </section>}
      {/* rooms you are both in (S4) and cards between you (S5) join here when those land */}
      {profile?.crm && profile.crm.fields.length > 0 && <section className="vx-s2-pp-notes" data-pp-sec="crm">
        <SectionLabel as="h3">Notes from {profile.crm.org}</SectionLabel>
        <dl>{profile.crm.fields.map((f) => <div key={f.label}><dt>{f.label}</dt><dd>{f.value}{f.only_you && <span className="vx-s2-only">only you see this</span>}</dd></div>)}</dl>
      </section>}
      {!p.workspaces.length && !together.length && !profile && <p className="vx-s2-note">{onVexa ? "You share no workspace with them yet." : "They have not joined Vexa. Invite them, and they can see what you share with them."}</p>}
    </article>
  );
}
