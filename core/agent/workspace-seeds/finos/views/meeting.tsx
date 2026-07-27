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

// Attendance is reported once, when the meeting ends — so during a live call the section
// explains itself rather than showing an empty table.
function clockTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? "—" : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function duration(seconds) {
  if (typeof seconds !== "number" || seconds < 0) return "—";
  const mins = Math.round(seconds / 60);
  if (mins < 1) return "< 1 min";
  if (mins < 60) return mins + " min";
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return m ? h + "h " + m + "m" : h + "h";
}

export default function MeetingCanvas() {
  const meeting = useMeeting();
  const notes = useMeetingNotes();
  const cleanNotes = notes.map((note) => ({ ...note, chapter: "" }));

  const status = meeting.meeting.status || "live";
  const title = meeting.meeting.title || "Meeting";

  // Who was actually in the room, ordered by arrival — a late joiner and an early leaver read
  // differently from someone present throughout, which is the whole point of keeping intervals.
  const attendance = meeting.meeting.attendance || [];
  const attendanceRows = attendance.map((p) => [
    p.name,
    clockTime(p.first_seen),
    clockTime(p.last_seen),
    duration(p.present_seconds),
  ]);
  const isLive = String(status).toLowerCase() === "live" || String(status).toLowerCase() === "active";

  return (
    <ui.Stack size="lg">
      <ui.Row align="left" size="sm">
        <ui.Badge tone={statusTone(status)}>{statusLabel(status)}</ui.Badge>
        <ui.Tag tone="default">{title}</ui.Tag>
        {attendance.length ? <ui.Tag tone="default">{attendance.length + " attended"}</ui.Tag> : null}
      </ui.Row>

      <ui.Section title="Attendance">
        <ui.Table
          columns={["Name", "Arrived", "Left", "Present"]}
          rows={attendanceRows}
          empty={isLive
            ? "Attendance is recorded when the meeting ends."
            : "No attendance was recorded for this meeting."}
        />
      </ui.Section>

      <ui.Section title="Transcript">
        <ui.LiveNotes notes={cleanNotes} maxNotes={80} merge empty="Clean attributed transcript appears as people speak." />
      </ui.Section>
    </ui.Stack>
  );
}
