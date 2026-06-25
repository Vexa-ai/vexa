// Harness-governed Meeting Canvas view. The agent may rewrite this file; the terminal validates it before execution.
export default function MeetingCanvas() {
  const meeting = useMeeting();
  const actions = useActions();
  const participantCount = meeting.meeting.participants?.length ?? 0;
  const numbers = meeting.entities.numbers.map((n, index) => ({
    item: n.text ?? String(n.value ?? index + 1),
    value: n.value ?? "",
  }));

  return (
    <ui.Col size="lg">
      <ui.Panel title={meeting.meeting.title} subtitle="Live meeting feed rendered through the Meeting Canvas harness">
        <ui.Grid columns={3}>
          <ui.Stat label="Participants" value={participantCount} tone="accent" />
          <ui.Stat label="Cards" value={meeting.cards.length} tone="green" />
          <ui.Stat label="Segments" value={meeting.transcript.segments.length} />
        </ui.Grid>
      </ui.Panel>

      <ui.Grid columns={2}>
        <ui.Section title="Cards">
          <ui.Timeline items={meeting.cards.map((card) => ({
            id: card.id,
            title: card.title,
            body: card.body,
            ts: card.ts,
            kind: card.kind,
          }))} />
        </ui.Section>

        <ui.Section title="Numbers">
          <ui.Table
            columns={[{ key: "item", label: "Item" }, { key: "value", label: "Value", align: "right" }]}
            rows={numbers}
            empty="No surfaced numbers yet"
          />
        </ui.Section>
      </ui.Grid>

      <ui.Section title="Transcript">
        <ui.Transcript segments={meeting.transcript.segments.slice(-18)} liveCaption={meeting.transcript.liveCaption} />
      </ui.Section>

      <ui.Row align="right">
        <ui.Button tone="accent" onClick={() => actions.note(`Canvas snapshot: ${meeting.cards.length} cards, ${meeting.transcript.segments.length} transcript segments`)}>
          Note snapshot
        </ui.Button>
      </ui.Row>
    </ui.Col>
  );
}
