// Harness-governed Meeting Canvas view. The agent may rewrite this file; the terminal trial-renders it before promotion.
export default function MeetingCanvas() {
  const { segments, liveCaption } = useTranscript({ by: "time", window: 24 });
  const speakers = useSpeakers();
  const entities = useEntities();
  const actions = useActions();

  const speakerItems = speakers.slice(0, 6).map((speaker) => ({
    title: speaker.name,
    body: `${speaker.segments} transcript segments`,
    meta: `${speaker.talkPct}%`,
    tone: speaker.talkPct >= 40 ? "accent" : "default",
  }));

  const entityRows = entities.slice(0, 24).map((entity) => ({
    kind: entity.kind,
    name: entity.title,
    detail: entity.subtitle || entity.value || entity.body || "",
  }));

  return (
    <ui.Stack size="lg">
      <ui.Panel title="Meeting Canvas" subtitle="Safe live view rendered through the Meeting Canvas harness">
        <ui.Grid columns={3}>
          <ui.Stat label="Speakers" value={speakers.length} tone="accent" />
          <ui.Stat label="Entities" value={entities.length} tone="green" />
          <ui.Stat label="Transcript" value={segments.length} />
        </ui.Grid>
      </ui.Panel>

      <ui.Grid columns={2}>
        <ui.Section title="Speakers">
          <ui.List items={speakerItems} empty="No speakers yet" />
        </ui.Section>

        <ui.Section title="Entities">
          <ui.Table
            columns={[
              { key: "kind", label: "Kind" },
              { key: "name", label: "Name" },
              { key: "detail", label: "Detail" },
            ]}
            rows={entityRows}
            empty="No entities yet"
          />
        </ui.Section>
      </ui.Grid>

      <ui.Section title="Transcript">
        <ui.Transcript segments={segments} liveCaption={liveCaption} />
      </ui.Section>

      <ui.Row align="right">
        <ui.Button tone="accent" onClick={() => actions.note(`Canvas snapshot: ${speakers.length} speakers, ${entities.length} entities, ${segments.length} transcript segments`)}>
          Note snapshot
        </ui.Button>
      </ui.Row>
    </ui.Stack>
  );
}
