// Harness-governed Meeting Canvas view. The terminal trial-renders this before promotion.
// Live FIRST-PERSON notes (the transcript condensed in the speaker's own words, folded in every few
// utterances, with inline keyword tags) over the live raw-transcript TAIL.
function statusLabel(status) {
  const v = String(status || "live").toLowerCase();
  if (v === "active" || v === "live") return "Live";
  if (v === "awaiting_admission") return "Waiting";
  if (v === "needs_help") return "Help";
  if (v === "completed" || v === "past") return "Done";
  return v ? v[0].toUpperCase() + v.slice(1).replace(/_/g, " ") : "Live";
}

function statusTone(status) {
  const v = String(status || "live").toLowerCase();
  if (v === "active" || v === "live") return "green";
  if (v === "completed" || v === "past") return "green";
  if (v === "scheduled" || v === "joining" || v === "awaiting_admission") return "accent";
  return "warn";
}

export default function MeetingCanvas() {
  const meeting = useMeeting();
  const transcript = useTranscript({ by: "time" });
  const notes = useMeetingNotes();

  const status = meeting.meeting.status || "live";
  const title = meeting.meeting.title || "Meeting";

  return (
    <ui.Stack size="lg">
      <ui.Row align="left" size="sm">
        <ui.Badge tone={statusTone(status)}>{statusLabel(status)}</ui.Badge>
        <ui.Tag tone="default">{title}</ui.Tag>
      </ui.Row>

      <ui.Section title="Live notes">
        <ui.LiveNotes notes={notes} empty="Condensing the conversation — notes appear as people speak." />
      </ui.Section>

      <ui.LiveTranscript
        segments={transcript.segments}
        liveCaption={transcript.liveCaption}
        maxSegments={3}
        empty="waiting for the live transcript…"
      />
    </ui.Stack>
  );
}
