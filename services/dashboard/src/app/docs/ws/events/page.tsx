import { Metadata } from "next";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export const metadata: Metadata = {
  title: "WebSocket Events | Vexa API Documentation",
  description: "Reference for WebSocket event types and message formats",
};

export default function WebSocketEventsPage() {
  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">WebSocket Events</h1>
        <p className="text-muted-foreground mt-2">
          Reference for all WebSocket message types and their formats.
        </p>
      </div>

      {/* Transcript Mutable */}
      <Card>
        <CardHeader>
          <CardTitle>transcript.mutable</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm mb-4">
            Live transcript segments that may be updated as the AI refines its transcription.
          </p>
          <pre className="bg-muted p-4 rounded-lg text-sm overflow-x-auto">
            <code>{JSON.stringify({
              type: "transcript.mutable",
              meeting: { id: 123 },
              payload: {
                segments: [
                  {
                    text: "Hello everyone",
                    speaker: "Alice",
                    language: "en",
                    start: 0.0,
                    end_time: 2.5,
                    absolute_start_time: "2024-01-01T12:00:00Z",
                    absolute_end_time: "2024-01-01T12:00:02Z",
                    session_uid: "session-123",
                    updated_at: "2024-01-01T12:00:02Z",
                  },
                ],
              },
              ts: "2024-01-01T12:00:02Z",
            }, null, 2)}</code>
          </pre>
        </CardContent>
      </Card>

      {/* Transcript Finalized */}
      <Card>
        <CardHeader>
          <CardTitle>transcript.finalized</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm mb-4">
            Final transcript segments that will not be updated further.
          </p>
          <pre className="bg-muted p-4 rounded-lg text-sm overflow-x-auto">
            <code>{JSON.stringify({
              type: "transcript.finalized",
              meeting: { id: 123 },
              payload: {
                segments: [
                  {
                    text: "Hello everyone, welcome to the meeting.",
                    speaker: "Alice",
                    language: "en",
                    start: 0.0,
                    end_time: 3.2,
                    absolute_start_time: "2024-01-01T12:00:00Z",
                    absolute_end_time: "2024-01-01T12:00:03Z",
                    session_uid: "session-123",
                    updated_at: "2024-01-01T12:00:03Z",
                  },
                ],
              },
              ts: "2024-01-01T12:00:03Z",
            }, null, 2)}</code>
          </pre>
        </CardContent>
      </Card>

      {/* Meeting Status */}
      <Card>
        <CardHeader>
          <CardTitle>meeting.status</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm mb-4">
            Meeting status updates. Sent when the meeting status changes. This is the primary way to track bot progress from creation to completion.
          </p>
          <pre className="bg-muted p-4 rounded-lg text-sm overflow-x-auto">
            <code>{JSON.stringify({
              type: "meeting.status",
              meeting: {
                platform: "google_meet",
                native_id: "abc-defg-hij",
              },
              payload: {
                status: "active",
              },
              ts: "2024-01-01T12:00:05Z",
            }, null, 2)}</code>
          </pre>
          
          <div className="mt-6 space-y-4">
            <div>
              <h4 className="text-sm font-semibold mb-2">Status Flow</h4>
              <p className="text-sm text-muted-foreground mb-3">
                The bot progresses through these statuses in order:
              </p>
              <ol className="list-decimal list-inside space-y-2 text-sm">
                <li>
                  <code className="bg-muted px-1.5 py-0.5 rounded">requested</code> - Bot creation requested, container starting up
                </li>
                <li>
                  <code className="bg-muted px-1.5 py-0.5 rounded">joining</code> - Bot is connecting to the meeting platform
                </li>
                <li>
                  <code className="bg-muted px-1.5 py-0.5 rounded">awaiting_admission</code> - Bot is in the meeting waiting room, waiting for host to admit
                </li>
                <li>
                  <code className="bg-muted px-1.5 py-0.5 rounded">active</code> - Bot is admitted and actively transcribing
                </li>
                <li>
                  <code className="bg-muted px-1.5 py-0.5 rounded">completed</code> - Transcription finished (meeting ended or bot stopped)
                </li>
                <li>
                  <code className="bg-muted px-1.5 py-0.5 rounded">failed</code> - Bot failed to join or transcription error occurred
                </li>
              </ol>
            </div>

            <div>
              <h4 className="text-sm font-semibold mb-2">Status Descriptions</h4>
              <dl className="space-y-2 text-sm">
                <div>
                  <dt className="font-medium inline">requested:</dt>
                  <dd className="inline text-muted-foreground ml-2">Bot container is being created and initialized. Usually takes 10-30 seconds.</dd>
                </div>
                <div>
                  <dt className="font-medium inline">joining:</dt>
                  <dd className="inline text-muted-foreground ml-2">Bot is launching the browser and connecting to the meeting platform. Usually takes 5-15 seconds.</dd>
                </div>
                <div>
                  <dt className="font-medium inline">awaiting_admission:</dt>
                  <dd className="inline text-muted-foreground ml-2">Bot is in the meeting waiting room. The meeting host must admit the bot. This can take any amount of time depending on when the host checks the waiting room.</dd>
                </div>
                <div>
                  <dt className="font-medium inline">active:</dt>
                  <dd className="inline text-muted-foreground ml-2">Bot is admitted to the meeting and actively transcribing audio. You will receive <code className="bg-muted px-1 rounded">transcript.mutable</code> and <code className="bg-muted px-1 rounded">transcript.finalized</code> events during this state.</dd>
                </div>
                <div>
                  <dt className="font-medium inline">completed:</dt>
                  <dd className="inline text-muted-foreground ml-2">Transcription has finished. The meeting ended or the bot was stopped. Final transcripts are available via REST API.</dd>
                </div>
                <div>
                  <dt className="font-medium inline">failed:</dt>
                  <dd className="inline text-muted-foreground ml-2">An error occurred. Common reasons: admission timeout, meeting ended before bot joined, connection failure, or bot was removed.</dd>
                </div>
              </dl>
            </div>

            <div>
              <h4 className="text-sm font-semibold mb-2">Example: Status Progression</h4>
              <pre className="bg-muted p-4 rounded-lg text-sm overflow-x-auto">
                <code>{`// 1. Bot created
{
  "type": "meeting.status",
  "payload": { "status": "requested" },
  "ts": "2024-01-01T12:00:00Z"
}

// 2. Bot connecting
{
  "type": "meeting.status",
  "payload": { "status": "joining" },
  "ts": "2024-01-01T12:00:15Z"
}

// 3. Bot in waiting room
{
  "type": "meeting.status",
  "payload": { "status": "awaiting_admission" },
  "ts": "2024-01-01T12:00:30Z"
}

// 4. Bot admitted and transcribing
{
  "type": "meeting.status",
  "payload": { "status": "active" },
  "ts": "2024-01-01T12:01:00Z"
}

// 5. Meeting ended
{
  "type": "meeting.status",
  "payload": { "status": "completed" },
  "ts": "2024-01-01T13:00:00Z"
}`}</code>
              </pre>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Subscribed */}
      <Card>
        <CardHeader>
          <CardTitle>subscribed</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm mb-4">
            Confirmation message sent after successfully subscribing to meetings.
          </p>
          <pre className="bg-muted p-4 rounded-lg text-sm overflow-x-auto">
            <code>{JSON.stringify({
              type: "subscribed",
              meetings: [123, 124],
            }, null, 2)}</code>
          </pre>
        </CardContent>
      </Card>

      {/* Pong */}
      <Card>
        <CardHeader>
          <CardTitle>pong</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm mb-4">
            Response to ping messages. Confirms the connection is alive.
          </p>
          <pre className="bg-muted p-4 rounded-lg text-sm overflow-x-auto">
            <code>{JSON.stringify({
              type: "pong",
            }, null, 2)}</code>
          </pre>
        </CardContent>
      </Card>

      {/* Error */}
      <Card>
        <CardHeader>
          <CardTitle>error</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm mb-4">
            Error messages sent when something goes wrong.
          </p>
          <pre className="bg-muted p-4 rounded-lg text-sm overflow-x-auto">
            <code>{JSON.stringify({
              type: "error",
              message: "Invalid meeting ID",
            }, null, 2)}</code>
          </pre>
        </CardContent>
      </Card>
    </div>
  );
}

