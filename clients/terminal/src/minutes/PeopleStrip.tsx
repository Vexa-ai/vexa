"use client";
/** THE PEOPLE ON THE HOME (founder, 2026-10-15: "we need to have all people we have interacted with
 *  on the main page"). Everyone the person met, chatted with, shares a workspace with — one server
 *  set (`GET /api/people/known`, the same the @ picker offers), most recent first: a compact
 *  wrapping strip of faces and names, then "All people (N)" for the full list with search and a
 *  filter by how they know them. Hovering a person shows their short story; a click opens their page;
 *  a person with no page shows how they are known (the agent makes the page when asked). */
import { useEffect, useMemo, useState } from "react";
import { OPEN_ENTITY_EVENT } from "../platform";
import { Avatar } from "../ui-kit";
import { useHoverCard } from "../ui-kit/hoverCard";
import { hoverStory } from "../ui-kit/hoverFacts";
import { readWorkspaceFile } from "../data/workspaceApi";
import { listKnownPeople, type KnownPerson } from "../surfaces/sessionsApi";

export const STRIP_MAX = 12;
const HOW_LABEL: Record<string, string> = { met: "Met", chat: "Chatted", workspace: "Shares a workspace", mail: "Emailed", page: "Has a page" };

function openPage(p: KnownPerson) {
  if (!p.page) return;
  window.dispatchEvent(new CustomEvent(OPEN_ENTITY_EVENT, { detail: { path: p.page } }));
}

function PersonFace({ p }: { p: KnownPerson }) {
  const sub = p.how_words ? p.how_words[0].toUpperCase() + p.how_words.slice(1) : "";
  const hover = useHoverCard({
    name: p.name, mark: <Avatar p={{ name: p.name, initials: p.person.initials ?? p.name.slice(0, 2).toUpperCase(), photo: p.person.photo, avatar: p.person.photo ? undefined : p.person.avatar }} />,
    cacheKey: `known:${p.key}`,
    load: async () => {
      if (!p.page) return sub ? [sub] : [];
      const story = hoverStory(String((await readWorkspaceFile(p.page).catch(() => "")) ?? ""));
      return story ? [story] : sub ? [sub] : [];
    },
  });
  return (
    <>
      <button type="button" className="vx-people-face" data-person-key={p.key} data-has-page={p.page ? "" : undefined}
        {...hover.bind} onClick={() => openPage(p)}>
        <Avatar p={{ name: p.name, initials: p.person.initials ?? p.name.slice(0, 2).toUpperCase(), photo: p.person.photo, avatar: p.person.photo ? undefined : p.person.avatar }} />
        <span className="vx-people-name">{p.name}</span>
      </button>
      {hover.card}
    </>
  );
}

/** Pure: the people for a search and a "how you know them" filter. */
export function filterPeople(rows: KnownPerson[], q: string, how: string | null): KnownPerson[] {
  const n = q.trim().toLowerCase();
  return rows.filter((r) => (!how || r.how.includes(how))
    && (!n || `${r.name} ${r.person.email ?? ""} ${r.person.role ?? ""}`.toLowerCase().includes(n)));
}

function AllPeople({ rows, onClose }: { rows: KnownPerson[]; onClose: () => void }) {
  const [q, setQ] = useState("");
  const [how, setHow] = useState<string | null>(null);
  const kinds = useMemo(() => Object.keys(HOW_LABEL).filter((k) => rows.some((r) => r.how.includes(k))), [rows]);
  const shown = filterPeople(rows, q, how);
  return (
    <div className="vx-people-all" role="dialog" aria-label="All people" data-people-all=""
      onKeyDown={(e) => { if (e.key === "Escape") onClose(); }}>
      <div className="vx-people-all-head">
        <input autoFocus aria-label="Search people" placeholder="Search people" value={q} onChange={(e) => setQ(e.target.value)} className="vx-people-search" />
        <button type="button" className="vx-people-close" aria-label="Close" onClick={onClose}>×</button>
      </div>
      <div className="vx-people-filters" role="group" aria-label="How you know them">
        <button type="button" data-how="" aria-pressed={how === null} onClick={() => setHow(null)}>Everyone</button>
        {kinds.map((k) => <button key={k} type="button" data-how={k} aria-pressed={how === k} onClick={() => setHow(k)}>{HOW_LABEL[k]}</button>)}
      </div>
      <div className="vx-people-grid">{shown.map((p) => <PersonFace key={p.key} p={p} />)}</div>
    </div>
  );
}

export function PeopleStrip() {
  const [rows, setRows] = useState<KnownPerson[] | null>(null);
  const [all, setAll] = useState(false);
  useEffect(() => {
    let live = true;
    void listKnownPeople().then((r) => { if (live) setRows(r.people); }).catch(() => { if (live) setRows([]); });
    return () => { live = false; };
  }, []);
  if (!rows || rows.length === 0) return null;
  return (
    <section className="vx-people-strip" aria-label="People" data-people-strip="">
      <div className="vx-people-faces">
        {rows.slice(0, STRIP_MAX).map((p) => <PersonFace key={p.key} p={p} />)}
        {rows.length > STRIP_MAX && (
          <button type="button" className="vx-people-more" data-people-more="" onClick={() => setAll(true)}>All people ({rows.length})</button>
        )}
      </div>
      {all && <AllPeople rows={rows} onClose={() => setAll(false)} />}
    </section>
  );
}
