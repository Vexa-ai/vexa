"use client";
/** EVERY PERSON CHIP AND AVATAR OPENS THE PERSON (UI 7mm; founder 2026-10-18: the names in the meeting,
 *  prep and room people rows were unclickable). A click opens their profile on the right (Message ·
 *  Start call · shared workspaces · Copy reference); hovering shows a small card with the same doors.
 *  The person is addressed by their account or address when known — never by a guess from a name the
 *  profile cannot resolve (an unknown name opens the profile's own "not on Vexa yet" state). */
import { useRef, useState, type DragEvent, type ReactNode } from "react";
import { copyReference } from "../../surfaces/reference";
import { openPerson } from "./personOpen";

export type PersonLike = { name: string; subject?: string | null; email?: string | null; key?: string | null };

/** The person's full reference target, when they have an id to reference. */
export function personTarget(p: PersonLike): { kind: "person"; id: string; label: string } | null {
  const id = p.email || p.subject || "";
  return id ? { kind: "person", id, label: p.name } : null;
}

export function PersonChip({ person, children, className }: { person: PersonLike; children?: ReactNode; className?: string }) {
  const [card, setCard] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const target = personTarget(person);
  const open = () => { setCard(false); openPerson(person); };
  const hover = (on: boolean) => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setCard(on), on ? 350 : 150);
  };
  return (
    <span className={`vx-person-chip${className ? ` ${className}` : ""}`} data-person-chip={person.subject || person.email || person.name}
      onMouseEnter={() => hover(true)} onMouseLeave={() => hover(false)}
      {...(target ? { draggable: true, onDragStart: (e: DragEvent) => { const c = `[[person:${target.id}]]`; e.dataTransfer?.setData("text/plain", c); e.dataTransfer?.setData("application/x-vexa-ref", c); } } : {})}>
      <span role="button" tabIndex={0} className="vx-person-chip-hit" title={`Open ${person.name}`} aria-label={`Open ${person.name}`}
        onClick={(e) => { e.stopPropagation(); open(); }} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } }}>
        {children ?? person.name}
      </span>
      {card && (
        <span className="vx-person-card" role="dialog" aria-label={person.name} data-person-card="">
          <b className="vx-person-card-name">{person.name}</b>
          {person.email && <small className="vx-person-card-sub">{person.email}</small>}
          <span className="vx-person-card-acts">
            <button type="button" className="vx-btn" data-variant="ghost" data-size="sm" onClick={open}>Open profile</button>
            {target && <button type="button" className="vx-btn" data-variant="ghost" data-size="sm" data-copy-ref={`[[person:${target.id}]]`}
              onClick={() => { void copyReference(target); }}>Copy reference</button>}
          </span>
        </span>
      )}
    </span>
  );
}
