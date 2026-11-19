"use client";
/** THE TOP OF THE SIDEBAR (UI 7k "one list", founder 2026-10-17, Claude desktop as the reference;
 *  feature `shell2`): one list of same-style rows — + New · Inbox · Meetings · People — and no "Chats" header.
 *  "+ New" is a row, not a header button: it opens the FULL home with a fresh chat and resets the
 *  target to My desk (7t, "a remembered target never carries over"). Inbox and Meetings open their
 *  lists in the centre. There is no Search row: the product has no search yet (7k: "Search if
 *  present"). */
import { CalendarDays, Inbox as InboxIcon, Plus, Users } from "lucide-react";
import { ListRow } from "../../ui-kit";
import { useAttention } from "../desktop/home2/attentionApi";
import { useWorkspaceSettings } from "../desktop/home2/settingsApi";
import { openCentre, useCentre } from "./centre";
import { inboxCount, inboxNeeds } from "./inbox";
import "./shell2.css";

export function SidebarNav({ onNew, onNavigate }: { onNew: () => void; onNavigate?: () => void }) {
  const on = useCentre();
  const { data } = useAttention();
  const settings = useWorkspaceSettings();
  const n = inboxCount(inboxNeeds(data?.needs ?? [], (s) => settings.of(s).muted || settings.of(s).hidden));
  return (
    <div className="vx-s2-nav" role="navigation" aria-label="New, Inbox, Meetings, People" data-shell2-nav="">
      <div data-s2-nav="new"><ListRow leading={<Plus size={14} strokeWidth={2} />} title="New" onSelect={() => { onNavigate?.(); onNew(); }} /></div>
      <div data-s2-nav="inbox"><ListRow leading={<InboxIcon size={14} strokeWidth={1.75} />} title="Inbox" selected={on === "inbox"}
        meta={n ? <span className="vx-s2-count">{n}</span> : undefined}
        onSelect={() => { openCentre("inbox"); onNavigate?.(); }} /></div>
      <div data-s2-nav="meetings"><ListRow leading={<CalendarDays size={14} strokeWidth={1.75} />} title="Meetings" selected={on === "meetings"}
        onSelect={() => { openCentre("meetings"); onNavigate?.(); }} /></div>
      <div data-s2-nav="people"><ListRow leading={<Users size={14} strokeWidth={1.75} />} title="People" selected={on === "people"}
        onSelect={() => { openCentre("people"); onNavigate?.(); }} /></div>
    </div>
  );
}
