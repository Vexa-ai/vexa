"use client";
/** A PERSON IS A LIVE REFERENCE (UI 7mm; founder 2026-10-18: "those guys are unclickable"). Every person
 *  name or face renders through this — the product version of the lab's `PersonRef` (feature
 *  `people_refs`, app-grounded-lab): a click opens the person's profile (Message · Start call · the
 *  workspaces you share · Copy reference — `openPerson`), a hover shows the shared entity hover card
 *  (their headline and company from the profile they claimed, or their address), and a drag carries
 *  their reference. A name alone is resolved to the account through the people you share workspaces
 *  with; one nobody resolves opens the profile's own "not on Vexa yet" state — never a guess. */
import type { DragEvent, ReactNode } from "react";
import { Identity } from "../../ui-kit";
import { useHoverCard } from "../../ui-kit/hoverCard";
import { openMyProfile, openPerson } from "./personOpen";
import { isMe } from "./me";

export type PersonLike = { name: string; subject?: string | null; email?: string | null; key?: string | null };

/** The person's full reference target, when they have an id to reference. */
export function personTarget(p: PersonLike): { kind: "person"; id: string; label: string } | null {
  const id = p.email || p.subject || "";
  return id ? { kind: "person", id, label: p.name } : null;
}

let peers: Promise<{ subject: string; name: string; email?: string }[]> | null = null;
/** Tests: forget the roster read. */
export function resetPersonPeers(): void { peers = null; }
/** A name alone → the account, through the people you share workspaces with (exact name, else first name). */
export async function subjectOfName(name: string): Promise<string | undefined> {
  peers ??= fetch("/api/people/peers", { cache: "no-store" }).then((r) => (r.ok ? r.json() : { people: [] }))
    .then((x: { people?: { subject: string; name: string }[] }) => x.people ?? []).catch(() => []);
  const n = name.trim().toLowerCase();
  if (!n) return undefined;
  const all = await peers;
  return (all.find((p) => p.name.toLowerCase() === n) ?? all.find((p) => p.name.toLowerCase().split(" ")[0] === n))?.subject;
}

export async function openPersonRef(p: PersonLike): Promise<void> {
  if (isMe({ name: p.name, subject: p.subject ?? undefined, email: p.email ?? undefined })) { openMyProfile(p.name); return; }
  const subject = p.subject || (p.email ? undefined : await subjectOfName(p.name));
  openPerson({ name: p.name, subject: subject ?? null, email: p.email ?? null, key: p.key ?? null });
}

async function cardLines(p: PersonLike): Promise<string[]> {
  const subject = p.subject || (p.email ? "" : await subjectOfName(p.name)) || "";
  const lines: string[] = [];
  if (subject) {
    try {
      const r = await fetch(`/api/people/${encodeURIComponent(subject)}/profile`, { cache: "no-store" });
      const j = r.ok ? await r.json() as { profile?: { headline?: string; company?: string } | null } : null;
      const pr = j?.profile;
      if (pr?.headline || pr?.company) lines.push([pr.headline, pr.company].filter(Boolean).join(" · "));
    } catch { /* the card shows what it has */ }
  }
  if (p.email) lines.push(p.email);
  lines.push("Click to open their profile");
  return lines;
}

export function PersonChip({ person, children, className }: { person: PersonLike; children?: ReactNode; className?: string }) {
  const target = personTarget(person);
  const hover = useHoverCard({
    name: person.name, mark: <Identity kind="person" id={person.subject || person.email || person.name} name={person.name} size={28} />,
    cacheKey: `person-card:${person.subject || person.email || person.name}`, load: () => cardLines(person),
  });
  const open = () => { void openPersonRef(person); };
  const drag = target ? { draggable: true, onDragStart: (e: DragEvent) => {
    const c = `[[person:${target.id}]]`;
    e.dataTransfer?.setData("text/plain", c); e.dataTransfer?.setData("application/x-vexa-ref", c);
  } } : {};
  return <>
    <span role="button" tabIndex={0} className={`vx-person-chip${className ? ` ${className}` : ""}`}
      data-person-chip={person.subject || person.email || person.name} aria-label={`Open ${person.name}`} {...hover.bind} {...drag}
      onClick={(e) => { e.stopPropagation(); open(); }}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } }}>
      {children ?? person.name}
    </span>
    {hover.card}
  </>;
}

/** The lab's name for it. */
export const PersonRef = PersonChip;
