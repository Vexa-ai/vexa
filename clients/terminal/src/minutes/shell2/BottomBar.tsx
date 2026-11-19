"use client";
/** THE PHONE'S BOTTOM BAR (decision 10, 2026-10-17; feature `shell2`, single mode only): Home ·
 *  Inbox · Meetings · People (founder 2026-10-17). The chats and folders are in the drawer, opened
 *  from the header's menu control at the top left, as ui-design §3 has it. Amends ui-design §3 for single
 *  mode. Each target is at least 44px tall on touch. */
import type { ReactNode } from "react";
import { CalendarDays, Home, Inbox, Users } from "lucide-react";
import { useAttention } from "../desktop/home2/attentionApi";
import { useWorkspaceSettings } from "../desktop/home2/settingsApi";
import { openCentre, useCentre } from "./centre";
import { inboxCount, inboxNeeds } from "./inbox";
import "./shell2.css";

export function BottomBar({ onHome, home, chatsOpen }: { onHome: () => void; home: boolean; chatsOpen: boolean }) {
  const on = useCentre();
  const { data } = useAttention();
  const settings = useWorkspaceSettings();
  const n = inboxCount(inboxNeeds(data?.needs ?? [], (s) => settings.of(s).muted || settings.of(s).hidden));
  const item = (key: string, label: string, icon: ReactNode, act: () => void, current: boolean, badge?: number) =>
    <button type="button" className="vx-s2-tab" data-tab-key={key} aria-current={current ? "page" : undefined} onClick={act}
      aria-label={badge ? `${label}, ${badge} waiting on you` : label}>
      <span className="vx-s2-tab-i" aria-hidden>{icon}{badge ? <i className="vx-s2-tab-badge">{badge}</i> : null}</span>
      <span className="vx-s2-tab-l">{label}</span>
    </button>;
  return (
    <nav className="vx-s2-bottom" aria-label="Main" data-bottom-bar="">
      {item("home", "Home", <Home size={18} strokeWidth={1.75} />, onHome, home && !on && !chatsOpen)}
      {item("inbox", "Inbox", <Inbox size={18} strokeWidth={1.75} />, () => openCentre("inbox"), on === "inbox" && !chatsOpen, n)}
      {item("meetings", "Meetings", <CalendarDays size={18} strokeWidth={1.75} />, () => openCentre("meetings"), on === "meetings" && !chatsOpen)}
      {item("people", "People", <Users size={18} strokeWidth={1.75} />, () => openCentre("people"), on === "people" && !chatsOpen)}
    </nav>
  );
}
