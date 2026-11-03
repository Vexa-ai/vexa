"use client";
/** A person's name, anywhere — a participant, an attendee, a speaker, a chat line, a tile — is one
 *  click from their profile card. The opener comes from context, so every list stays presentational. */
import { createContext, useContext, type ReactNode } from "react";
import "./people.css";

export type OpenPerson = (id: string, name: string) => void;
export const PersonOpenContext = createContext<OpenPerson | null>(null);
export const PersonOpenProvider = ({ open, children }: { open: OpenPerson; children: ReactNode }) =>
  <PersonOpenContext.Provider value={open}>{children}</PersonOpenContext.Provider>;

export function PersonName({ id, name, className }: { id: string; name: string; className?: string }) {
  const open = useContext(PersonOpenContext);
  if (!open || id === "vexa") return <span className={className}>{name}</span>;
  return (
    <button type="button" className={`vx-person-name${className ? ` ${className}` : ""}`} data-person={id}
      title={`${name} — open profile`} onClick={(e) => { e.stopPropagation(); open(id, name); }}>{name}</button>
  );
}
