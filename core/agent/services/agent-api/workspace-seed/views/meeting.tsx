// Harness-governed Meeting Canvas view. The terminal trial-renders this file before promotion.
function statusLabel(status) {
  const value = String(status || "live").toLowerCase();
  if (value === "active" || value === "live") return "Live";
  if (value === "awaiting_admission") return "Waiting";
  if (value === "needs_help") return "Help";
  if (value === "completed" || value === "past") return "Done";
  return value ? value[0].toUpperCase() + value.slice(1).replace(/_/g, " ") : "Live";
}

function statusTone(status) {
  const value = String(status || "live").toLowerCase();
  if (value === "completed" || value === "past") return "green";
  if (value === "scheduled" || value === "joining" || value === "awaiting_admission") return "accent";
  return "warn";
}

function pad(value) {
  return String(value).padStart(2, "0");
}

function formatMs(ms) {
  const total = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `${pad(minutes)}:${pad(seconds)}`;
}

function transcriptClock(segments) {
  for (let index = segments.length - 1; index >= 0; index -= 1) {
    const ts = segments[index]?.ts;
    if (typeof ts === "string" && ts.trim()) return ts;
    if (typeof ts === "number" && Number.isFinite(ts)) return formatMs(ts);
  }
  return "00:00";
}

function elapsed(startedAt, segments, now) {
  const started = Date.parse(startedAt || "");
  if (Number.isFinite(started)) return formatMs(now - started);
  return transcriptClock(segments);
}

export default function MeetingCanvas() {
  const meeting = useMeeting();
  const transcript = useTranscript({ by: "time" });
  const people = useEntities({ kind: "person" });
  const companies = useEntities({ kind: "company" });
  const numbers = useEntities({ kind: "number" });
  const signals = useSignals();
  const docs = useMeetingDocs();
  const actions = useActions();
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  const status = meeting.meeting.status || "live";
  const title = meeting.meeting.title || "Sales call";
  const clock = elapsed(meeting.meeting.startedAt, transcript.segments, now);

  return (
    <ui.Stack size="lg">
      <ui.Row size="sm">
        <ui.Badge tone={statusTone(status)}>{statusLabel(status)}</ui.Badge>
        <ui.Badge>{title}</ui.Badge>
        <ui.Badge>{clock}</ui.Badge>
        <ui.Button size="sm" tone="accent" onClick={() => actions.openDoc(docs.brief.path)}>
          Brief
        </ui.Button>
        <ui.Button size="sm" disabled={!docs.report.present} onClick={() => actions.openDoc(docs.report.path)}>
          Report
        </ui.Button>
      </ui.Row>

      <ui.Section title="Surfaced">
        <ui.Grid columns={4} size="sm">
          <ui.Section title="People">
            <ui.EntityList items={people} empty="No people yet" />
          </ui.Section>
          <ui.Section title="Companies">
            <ui.EntityList items={companies} empty="No companies yet" />
          </ui.Section>
          <ui.Section title="Numbers">
            <ui.EntityList items={numbers} empty="No numbers yet" />
          </ui.Section>
          <ui.Section title="Signals">
            <ui.EntityList items={signals} empty="No signals yet" />
          </ui.Section>
        </ui.Grid>
      </ui.Section>

      <ui.LiveTranscript segments={transcript.segments} liveCaption={transcript.liveCaption} />
    </ui.Stack>
  );
}
