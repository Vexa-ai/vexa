"use client";
/** ONE ITEM THAT NEEDS YOU, AS ONE CALM ROW (UI 7m; feature `shell2`) on the kit's ListRow: a kind
 *  icon (no caps label), the title with its muted tail (workspace · when), and the row's action
 *  columns — the suggestion slot, ONE verb always in the same place, "⋯" with the rest. The row's
 *  click opens the item and never decides it; Decline is in ⋯, last, behind Undo (R11). Used by the
 *  Inbox, the Now strip and the workspace page. */
import { useState } from "react";
import { AtSign, CalendarClock, CircleAlert, FileCheck2, SquareCheck } from "lucide-react";
import { ListRow, RowActions, formatDate } from "../../ui-kit";
import type { Need } from "../desktop/home2/attentionApi";
import { humanStatus } from "../desktop/home2/humanStatus";
import { canDecline, declineWithUndo, openNeed, whoOf } from "./needs";
import { nameOrYou } from "./me";

const ICON = { proposal: FileCheck2, mention: AtSign, card: SquareCheck, upcoming: CalendarClock, failed: CircleAlert } as const;

export function NeedRow({ n, where, act, readOnly }: { n: Need; where?: string; act: (n: Need) => void; readOnly?: boolean }) {
  const [gone, setGone] = useState(false);
  if (gone) return null;
  const Icon = ICON[n.kind] ?? SquareCheck;
  const title = humanStatus(n.text);
  // the tail starts with WHO: "from Maya · Thu · Growth"; an upcoming call reads its own way
  const who = whoOf(n);
  const tail = [who ? `from ${nameOrYou(who)}` : "", n.at ? formatDate(n.at) : "", where].filter(Boolean).join(" · ");
  const more = [
    { key: "open", label: "Open", onSelect: () => openNeed(n) },
    ...(!readOnly && canDecline(n) ? [{ key: "decline", label: "Decline", separatorBefore: true, onSelect: () => declineWithUndo(n, setGone), data: { "data-decline": "" } }] : []),
  ];
  return (
    <div className="vx-s2-need" data-need={n.id} data-kind={n.kind} role="group" aria-label={title}>
      <ListRow size="md" strong leading={<Icon size={14} strokeWidth={1.75} />} title={title} detail={tail || undefined}
        label={`Open: ${title}`} onSelect={() => openNeed(n)}
        trailing={<RowActions verb={readOnly ? undefined : { label: n.act.label, onClick: () => act(n) }} more={more} moreLabel={`More for ${title}`} />} />
    </div>
  );
}
