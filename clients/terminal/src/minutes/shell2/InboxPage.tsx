"use client";
/** THE INBOX (UI 7k, 7m, 7y; feature `shell2`): everything that waits on the person, across their
 *  workspaces, as uniform rows — grouped Waiting on you · Mentions · Did not run · Coming up, with
 *  counts. It reads the attention model the home already reads (one read model: the sidebar badge,
 *  this header and the groups agree) and decides nothing itself. */
import { EmptyState, SectionLabel } from "../../ui-kit";
import { useAttention } from "../desktop/home2/attentionApi";
import { useNeedAct } from "../desktop/home2/StatusCard";
import { useWorkspaceSettings } from "../desktop/home2/settingsApi";
import { inboxCount, inboxGroups, inboxNeeds } from "./inbox";
import { useWorkspaceNames } from "./names";
import { NeedRow } from "./NeedRow";
import "./shell2.css";

export function InboxPage() {
  const { phase, data } = useAttention();
  const settings = useWorkspaceSettings();
  const wsName = useWorkspaceNames();
  const [act, dialog] = useNeedAct();
  const needs = inboxNeeds(data?.needs ?? [], (s) => settings.of(s).muted || settings.of(s).hidden);
  const groups = inboxGroups(needs);
  const n = inboxCount(needs);
  return (
    <div className="vx-s2-page vx-s2-inbox" data-inbox-page="" data-inbox-count={n}>
      <header className="vx-s2-pagehead"><span className="vx-s2-pagecount">{n ? `${n} waiting on you` : "Nothing waiting on you"}</span></header>
      {phase === "refused" || phase === "failed"
        ? <EmptyState>The Inbox could not be read just now. It tries again in a moment.</EmptyState>
        : groups.length === 0
          ? <EmptyState>{phase === "ready" ? "Nothing waits on you. New approvals, mentions and runs that need you land here." : "Loading…"}</EmptyState>
          : groups.map((g) => <section key={g.key} className="vx-s2-sec" data-inbox-group={g.key}>
            <SectionLabel as="h3">{g.label} · {g.needs.length}</SectionLabel>
            {g.needs.map((x) => <NeedRow key={x.id} n={x} where={wsName(x.workspace)} act={(y) => void act(y)} />)}
          </section>)}
      {dialog}
    </div>
  );
}
