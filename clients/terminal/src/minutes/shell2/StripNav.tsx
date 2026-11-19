"use client";
/** Inbox and Meetings on the 48px rail strip (compact and narrow windows; feature `shell2`): the
 *  one-list sidebar's two destinations stay one click away when the rail folds to icons. */
import { CalendarDays, Inbox, Users } from "lucide-react";
import { IconButton } from "../../ui-kit";
import { useAttention } from "../desktop/home2/attentionApi";
import { useWorkspaceSettings } from "../desktop/home2/settingsApi";
import { openCentre, useCentre } from "./centre";
import { inboxCount, inboxNeeds } from "./inbox";

export function StripNav() {
  const on = useCentre();
  const { data } = useAttention();
  const settings = useWorkspaceSettings();
  const n = inboxCount(inboxNeeds(data?.needs ?? [], (s) => settings.of(s).muted || settings.of(s).hidden));
  return <>
    <IconButton label={n ? `Inbox, ${n} waiting on you` : "Inbox"} size="md" pressed={on === "inbox"} data-strip-nav="inbox" onClick={() => openCentre("inbox")}>
      <Inbox size={16} strokeWidth={1.75} />
    </IconButton>
    <IconButton label="Meetings" size="md" pressed={on === "meetings"} data-strip-nav="meetings" onClick={() => openCentre("meetings")}>
      <CalendarDays size={16} strokeWidth={1.75} />
    </IconButton>
    <IconButton label="People" size="md" pressed={on === "people"} data-strip-nav="people" onClick={() => openCentre("people")}>
      <Users size={16} strokeWidth={1.75} />
    </IconButton>
  </>;
}
