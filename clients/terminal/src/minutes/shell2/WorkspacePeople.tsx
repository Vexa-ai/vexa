"use client";
/** A WORKSPACE IS ABOUT THE PEOPLE AROUND IT (founder 2026-10-17; feature `shell2`).
 *
 *  - `PeopleLine`: 3–5 named faces with presence, "Maya, Sara, Leo · 2 online" — each face opens the
 *    person; you read "you" and are never counted as "online".
 *  - `lastChange`: who changed it last, phrased as a person doing something ("Maya updated the
 *    roadmap · 20 min"), when the server names who; the change alone otherwise.
 *  - `WorkspacePeopleSection`: every member as a row (name, role, presence), opening the person. */
import type { WorkspaceMember } from "../../platform/desktop";
import { AvatarStack, ListRow, SectionLabel, formatDate, registerPersonPhotos } from "../../ui-kit";
import { personPhotoUrl } from "../../platform/identity";
import { firstOrYou, isMe, nameOrYou } from "./me";
import { openPerson } from "./personOpen";
import { presenceWord } from "./people";

const order = (ms: WorkspaceMember[]) => [...ms].sort((a, b) => (isMe(a) ? 1 : 0) - (isMe(b) ? 1 : 0)
  || (b.presence === "now" ? 2 : b.presence === "today" ? 1 : 0) - (a.presence === "now" ? 2 : a.presence === "today" ? 1 : 0));

/** Members are Vexa accounts: their photo is identity's photo route (monogram when there is none). */
function photos(ms: WorkspaceMember[]): void {
  const m: Record<string, string> = {};
  for (const x of ms) if (x.subject && x.name) m[x.name] = personPhotoUrl(x.subject);
  registerPersonPhotos(m);
}

export function PeopleLine({ members, max = 4 }: { members: WorkspaceMember[]; max?: number }) {
  if (!members.length) return null;
  photos(members);
  const ms = order(members);
  const online = ms.filter((m) => m.presence === "now" && !isMe(m)).length;
  const names = ms.filter((m) => !isMe(m)).slice(0, 3).map((m) => firstOrYou(m.name));
  const more = ms.filter((m) => !isMe(m)).length - names.length;
  const byName = new Map(ms.map((m) => [m.name, m]));
  return (
    <span className="vx-s2-wsppl" data-tile-inner="" data-people-line="">
      <AvatarStack names={ms.map((m) => m.name)} max={max} label={(n) => nameOrYou(n)}
        onOpen={(n) => { const m = byName.get(n); openPerson({ name: n, subject: m?.subject }); }} />
      <span className="vx-s2-wsppl-names">{names.join(", ")}{more > 0 ? ` +${more}` : ""}{online ? <> · <b className="vx-s2-online">{online} online</b></> : null}</span>
    </span>
  );
}

/** "Maya updated the roadmap · 20 min" — the person first when the server names them. */
export function lastChange(last: { line: string; at: number; by?: string } | null | undefined): string {
  if (!last?.line) return "";
  const who = last.by ? `${nameOrYou(last.by)} ` : "";
  const line = who ? last.line.charAt(0).toLowerCase() + last.line.slice(1) : last.line;
  // a line that already says when ("… · 20 min ago") is not given a second time
  return `${who}${line}${last.at && !line.includes(" · ") ? ` · ${formatDate(last.at < 1e12 ? last.at * 1000 : last.at)}` : ""}`;
}

export function WorkspacePeopleSection({ members, id = "people" }: { members: WorkspaceMember[]; id?: string }) {
  if (!members.length) return null;
  photos(members);
  return (
    <section className="vx-s2-sec" id={`wsp-${id}`} data-sec={id}>
      <SectionLabel as="h3">People · {members.length}</SectionLabel>
      {order(members).map((m) => <ListRow key={m.subject} size="md" strong
        leading={<AvatarStack names={[m.name]} max={1} label={(n) => nameOrYou(n)} />}
        title={nameOrYou(m.name)} detail={[m.role, presenceWord(m.presence ?? null)].filter(Boolean).join(" · ") || undefined}
        onSelect={() => openPerson({ name: m.name, subject: m.subject })} />)}
    </section>
  );
}
