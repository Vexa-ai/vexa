"use client";
/** PEOPLE (founder 2026-10-17: "I see no people in the interface"; Slack-like; feature `shell2`): a
 *  list in the centre (7k), on the kit rows. One row per human — photo, name, presence, company, the
 *  workspaces you share as chips (and their role there). Search; scopes In my workspaces (the
 *  default: everyone who shares a workspace with you) · Recent · Contacts (people you share no
 *  workspace with, only with the CRM on) · All, which groups Recent → My workspaces → Contacts.
 *  A row opens the person panel on the right. "Waiting on you · n" joins the row when cards land
 *  (S5); until then there is nothing to count, so nothing is shown. */
import { copyRefItem, refDrag } from "../readme/ObjectActions";
import { useMemo, useState } from "react";
import { MoreHorizontal } from "lucide-react";
import { Chip, EmptyState, EntityChip, Identity, Input, ListRow, Menu, RowCell, SectionLabel, Tabs } from "../../ui-kit";
import { useFeature } from "../../platform/features";
import { useDesktopState } from "../desktop/desktopApi";
import { openWorkspaceView } from "../desktop/home2/workspaceView";
import { WorkspaceMark } from "../rooms/WorkspaceMark";
import { media, personPhotoUrl } from "../../platform/identity";
import { useWorkspaceNames } from "./names";
import { companyOf, groupPeople, inScope, matchPerson, mergePeople, presenceWord, usePeopleRead, type Person, type Presence, type Scope } from "./people";
import { openPerson } from "./personOpen";
import "./shell2.css";

/** Presence by account, from the desktop's member lists (the read the home already makes). */
export function usePresence(): (subject: string) => Presence {
  const { desktop } = useDesktopState();
  const map = new Map<string, Presence>();
  for (const i of desktop?.sections.find((s) => s.id === "workspaces")?.items ?? []) {
    for (const m of ((i as { members?: { subject: string; presence?: Presence }[] }).members ?? [])) {
      const cur = map.get(m.subject);
      if (m.presence === "now" || (m.presence === "today" && cur !== "now")) map.set(m.subject, m.presence);
    }
  }
  return (s) => map.get(s) ?? null;
}

/** The photo route for a person: their own (identity's photo route) for a Vexa user; never a face
 *  for a CRM contact who is not on Vexa (15g). */
export const photoOf = (p: Pick<Person, "vexa" | "subject" | "photo" | "avatar">): string | undefined =>
  p.vexa ? (p.photo || (p.subject ? personPhotoUrl(p.subject) : p.avatar ? media(p.avatar) : undefined)) : undefined;

export function usePeople(): { phase: string; people: Person[] } {
  const read = usePeopleRead();
  const presence = usePresence();
  const people = useMemo(() => mergePeople(read.peers, read.known, presence), [read.peers, read.known, presence]);
  return { phase: read.phase, people };
}

/** "+N": the workspaces not drawn as chips, named in its tooltip; it opens the person, whose panel
 *  lists them all */
function MoreChip({ p, wsName, from, data }: { p: Person; wsName: (s: string) => string; from: number; data: "wide" | "narrow" }) {
  const rest = p.workspaces.slice(from);
  return <span className="vx-s2-chip-more" data-more={data}><Chip title={rest.map(wsName).join(", ")} onClick={() => openPerson(p)}>+{rest.length}</Chip></span>;
}

function PersonRow({ p, wsName }: { p: Person; wsName: (s: string) => string }) {
  const company = companyOf(p.email);
  const words = [presenceWord(p.presence), p.headline, company].filter(Boolean).join(" · ");
  const ws = p.workspaces.slice(0, 2);
  return (
    <div className="vx-s2-person" data-person={p.key} {...(p.email || p.subject ? refDrag({ kind: "person", id: p.email || p.subject || "" }) : {})}>
      <ListRow size="md" strong label={`Open ${p.name}`} onSelect={() => openPerson(p)}
        leading={<span className="vx-s2-face" data-presence={p.presence ?? undefined}><Identity kind="person" id={p.subject || p.email || p.name} name={p.name} size={20} user={p.vexa} src={photoOf(p)} logo={p.logo} /></span>}
        title={p.name} detail={words || (p.vexa ? undefined : "Not on Vexa yet")}
        trailing={<>
          {/* AT MOST TWO WHOLE CHIPS, then "+N" (its tooltip lists the rest); one chip and "+N" in a narrow
              pane. A chip is never cut: each has a width cap and truncates its own label, whole */}
          <RowCell w={240}>{ws.length > 0 && <span className="vx-s2-chips" data-chips={p.workspaces.length}>
            {ws.map((w, i) => <span key={w} className="vx-s2-chip" data-nth={i}><EntityChip kind="workspace" icon={<WorkspaceMark id={w} name={wsName(w)} size={16} />}
              title={`${wsName(w)}${p.roles[w] ? ` · ${p.roles[w]}` : ""}`} onOpen={() => void openWorkspaceView({ slug: w, name: wsName(w) })}>{wsName(w)}</EntityChip></span>)}
            {p.workspaces.length > 2 && <MoreChip p={p} wsName={wsName} from={2} data="wide" />}
            {p.workspaces.length > 1 && <MoreChip p={p} wsName={wsName} from={1} data="narrow" />}
          </span>}</RowCell>
          <RowCell w={28}><Menu label={`More for ${p.name}`} align="end" variant="ghost" trigger={<MoreHorizontal size={14} strokeWidth={1.75} aria-hidden />}
            items={[{ key: "open", label: "Open", onSelect: () => openPerson(p) },
              ...(p.email || p.subject ? [copyRefItem({ kind: "person", id: p.email || p.subject || "", label: p.name })] : [])]} /></RowCell>
        </>} />
    </div>
  );
}

export function PeoplePage() {
  const { phase, people } = usePeople();
  const wsName = useWorkspaceNames();
  const crmOn = useFeature("crm");
  const [scope, setScope] = useState<Scope>("workspaces");
  const [q, setQ] = useState("");
  const scopes: { key: Scope; label: string }[] = [
    { key: "workspaces", label: "In my workspaces" }, { key: "recent", label: "Recent" },
    ...(crmOn ? [{ key: "contacts" as Scope, label: "Contacts" }] : []), { key: "all", label: "All" },
  ];
  // without the CRM, nobody outside your workspaces is shown — "All" is your workspaces and recents
  const visible = people.filter((p) => crmOn || p.workspaces.length > 0 || scope === "recent");
  const shown = visible.filter((p) => inScope(p, scope) && matchPerson(p, q, wsName));
  const groups = scope === "all" ? groupPeople(shown)
    : [{ key: scope, label: "", people: shown.slice().sort((a, b) => (b.presence === "now" ? 1 : 0) - (a.presence === "now" ? 1 : 0) || a.name.localeCompare(b.name)) }];
  const online = visible.filter((p) => p.presence === "now").length;
  return (
    <div className="vx-s2-page vx-s2-people" data-people-page="">
      <header className="vx-s2-pagehead"><span className="vx-s2-pagecount">{visible.filter((p) => p.workspaces.length > 0).length} people in your workspaces{online ? ` · ${online} online` : ""}</span></header>
      <div className="vx-s2-tools">
        <Input label="Search people" hideLabel control="sm" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search by name, company or workspace" />
      </div>
      <div className="vx-s2-scope"><Tabs label="Which people" value={scope} onChange={(k) => setScope(k as Scope)} items={scopes} /></div>
      {phase !== "ready" && people.length === 0
        ? <EmptyState>{phase === "failed" ? "People could not be read just now." : "Loading…"}</EmptyState>
        : shown.length === 0
          ? <EmptyState>{q ? "Nobody matches that." : scope === "workspaces" ? "Nobody shares a workspace with you yet. Invite someone from a workspace's page." : "Nobody here yet."}</EmptyState>
          : groups.map((g) => <section key={g.key} className="vx-s2-sec" data-people-group={g.key}>
            {g.label && <SectionLabel as="h3">{g.label} · {g.people.length}</SectionLabel>}
            {g.people.map((p) => <PersonRow key={p.key} p={p} wsName={wsName} />)}
          </section>)}
    </div>
  );
}
