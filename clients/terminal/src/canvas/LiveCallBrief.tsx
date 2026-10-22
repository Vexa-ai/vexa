"use client";
/** What Vexa listens for in a live call, in the chat that listens to it: the meeting page's brief
 *  (`ListenLine`), editable by the meeting's owner. Rendered by the chat's live call banner through
 *  `LIVE_CALL_BRIEF_KIND`. */
import { useMeetingNotes } from "./MeetingView";
import { ListenLine } from "./ListenLine";
import { writeWorkspaceFile } from "../data/workspaceApi";
import { DESK_SLUG } from "../minutes/workspaceReadme";

export function LiveCallBrief({ meetingId, editable }: { meetingId: string; editable: boolean }) {
  const notes = useMeetingNotes(meetingId);
  if (notes.phase !== "ready") return null;
  return <ListenLine raw={notes.body} readonly={!editable || !!notes.readonly}
    onSave={async (next) => { await writeWorkspaceFile(notes.path, next, { slug: notes.workspace || DESK_SLUG }); notes.reload(); }} />;
}
