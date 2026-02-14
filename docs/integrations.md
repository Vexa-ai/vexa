# Integrations

Vexa is designed to be easy to plug into your existing workflow.

## Meeting Platforms (Supported)

<CardGroup cols={3}>
  <Card title="Google Meet" href="/platforms/google-meet">
    <img src="/assets/integrations/google-meet.svg" width="28" height="28" style={{ display: "inline-block", verticalAlign: "middle" }} alt="Google Meet" />{" "}
    Meeting code format + admission model.
  </Card>

  <Card title="Microsoft Teams" href="/platforms/microsoft-teams">
    <img src="/assets/integrations/microsoft-teams.svg" width="28" height="28" style={{ display: "inline-block", verticalAlign: "middle" }} alt="Microsoft Teams" />{" "}
    Numeric meeting ID + required `?p=` passcode.
  </Card>

  <Card title="Zoom (special)" href="/platforms/zoom">
    <img src="/assets/integrations/zoom.svg" width="28" height="28" style={{ display: "inline-block", verticalAlign: "middle" }} alt="Zoom" />{" "}
    Requires extra setup and usually Marketplace approval.
  </Card>
</CardGroup>

Meeting ID extraction (all platforms):

- [Meeting links & IDs](meeting-ids.md)
- [Bot overview](bot-overview.md)

## Available now

### ChatGPT

<img src="/assets/integrations/chatgpt.svg" width="28" height="28" style={{ display: "inline-block", verticalAlign: "middle" }} alt="ChatGPT" />{" "}
Share a transcript with a single URL and paste it into ChatGPT (or any LLM UI).

- [ChatGPT transcript share links](chatgpt-transcript-share-links.md)

### n8n

<img src="/assets/integrations/n8n.svg" width="28" height="28" style={{ display: "inline-block", verticalAlign: "middle" }} alt="n8n" />{" "}
Use webhooks + API calls to automate workflows from meetings and transcripts.

- Tutorial: https://vexa.ai/blog/google-meet-transcription-n8n-workflow

## General integration primitives

- **REST API**: create bots, read transcripts, fetch recordings
- **WebSocket**: stream transcript segments live
- **Webhooks**: get notified when meetings complete
