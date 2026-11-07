"use client";
/** THE CENTRE LIST'S FRAME (UI 7k; mobile fix 4; feature `shell2`): the Inbox or Meetings, in the
 *  conversation's column with a header of its own — the menu control in single mode, the list's name,
 *  the pages toggle where the panel is a sheet — so a list is never a trap on a phone. Esc or the
 *  system back closes it (`centre.ts`). */
import { useEffect, type ReactNode } from "react";
import { PanelHeader } from "../../ui-kit";
import { closeCentre, type CentreList } from "./centre";
import { CentreBody } from "./CentreBody";
import "./shell2.css";

const TITLE: Record<CentreList, string> = { inbox: "Inbox", meetings: "Meetings", people: "People" };

export function CentrePane({ kind, leading, trailing }: { kind: CentreList; leading?: ReactNode; trailing?: ReactNode }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented || document.querySelector("[aria-modal=true]")) return;
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
      closeCentre();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  return (
    <section className="vx-pane vx-s2-centre" data-pane="centre-list" data-centre={kind} aria-label={TITLE[kind]}>
      <PanelHeader title={<span className="vx-s2-centre-title">{leading}{TITLE[kind]}</span>} trailing={trailing} />
      <div className="vx-s2-centre-body"><CentreBody kind={kind} /></div>
    </section>
  );
}
