// Harness-governed Meeting Canvas view. The terminal trial-renders this before promotion.
// Live FIRST-PERSON transcript: model-cleaned lines plus local fallback lines in one attributed body.
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
  const notes = useMeetingNotes();
  const cleanNotes = notes.map((note) => ({ ...note, chapter: "" }));

  const status = meeting.meeting.status || "live";
  const title = meeting.meeting.title || "Meeting";

  return (
    <ui.Stack size="lg">
      <ui.Row align="left" size="sm">
        <ui.Badge tone={statusTone(status)}>{statusLabel(status)}</ui.Badge>
        <ui.Tag tone="default">{title}</ui.Tag>
      </ui.Row>

      <ui.Section title="Transcript">
        <ui.LiveNotes notes={cleanNotes} maxNotes={80} merge empty="Clean attributed transcript appears as people speak." />
      </ui.Section>
    </ui.Stack>
  );
}
