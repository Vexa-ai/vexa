"use client";
/** What each centre list draws (feature `shell2`). */
import type { CentreList } from "./centre";
import { InboxPage } from "./InboxPage";
import { MeetingsPage } from "./MeetingsPage";
import { PeoplePage } from "./PeoplePage";

export function CentreBody({ kind }: { kind: CentreList }) {
  return kind === "inbox" ? <InboxPage /> : kind === "people" ? <PeoplePage /> : <MeetingsPage />;
}
