import { Metadata } from "next";
import { APIEndpointDoc } from "@/components/docs/api-endpoint-doc";

export const metadata: Metadata = {
  title: "Transcripts API | Vexa API Documentation",
  description: "API reference for fetching and sharing meeting transcripts",
};

export default function TranscriptsPage() {
  return (
    <div className="space-y-12">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Transcripts API</h1>
        <p className="text-muted-foreground mt-2">
          Fetch transcripts, create shareable links, and export transcript data.
        </p>
      </div>

      {/* Get Transcripts */}
      <div id="get-transcripts">
        <APIEndpointDoc
          title="Get Transcripts"
        description="Get all transcript segments for a meeting. Returns both the transcript data and meeting metadata."
        method="GET"
        path="/transcripts/{platform}/{native_meeting_id}"
        authType="user"
        dashboardProxy="/api/vexa/transcripts/{platform}/{native_meeting_id}"
        pathParams={[
          {
            name: "platform",
            type: "string",
            description: "Meeting platform: google_meet or teams",
            required: true,
          },
          {
            name: "native_meeting_id",
            type: "string",
            description: "The platform-specific meeting ID",
            required: true,
          },
        ]}
        responseExample={{
          id: 123,
          platform: "google_meet",
          native_meeting_id: "abc-defg-hij",
          status: "completed",
          start_time: "2024-01-01T12:00:00Z",
          end_time: "2024-01-01T13:00:00Z",
          segments: [
            {
              start: 0.0,
              end: 5.2,
              text: "Hello everyone, welcome to today's meeting.",
              speaker: "Alice",
              language: "en",
              absolute_start_time: "2024-01-01T12:00:00Z",
              absolute_end_time: "2024-01-01T12:00:05Z",
              created_at: "2024-01-01T12:00:05Z",
            },
          ],
        }}
        errorExamples={[
          {
            status: 404,
            body: { error: "Meeting not found" },
            description: "The specified meeting does not exist",
          },
        ]}
        notes={[
          "Segments are returned in chronological order",
          "Each segment includes speaker identification, timestamps, and text",
          "For active meetings, segments may be incomplete (mutable)",
        ]}
        />
      </div>

      {/* Create Share Link */}
      <div id="create-transcript-share-link">
        <APIEndpointDoc
          title="Create Transcript Share Link"
        description="Create a short-lived public URL for sharing transcripts with external tools like ChatGPT or Perplexity."
        method="POST"
        path="/transcripts/{platform}/{native_meeting_id}/share"
        authType="user"
        dashboardProxy="/api/vexa/transcripts/{platform}/{native_meeting_id}/share"
        pathParams={[
          {
            name: "platform",
            type: "string",
            description: "Meeting platform: google_meet or teams",
            required: true,
          },
          {
            name: "native_meeting_id",
            type: "string",
            description: "The platform-specific meeting ID",
            required: true,
          },
        ]}
        queryParams={[
          {
            name: "meeting_id",
            type: "integer",
            description: "Optional meeting ID for reference",
            required: false,
          },
          {
            name: "ttl_seconds",
            type: "integer",
            description: "Time-to-live in seconds (default: 3600)",
            required: false,
          },
        ]}
        responseExample={{
          share_id: "abc123def456",
          url: "https://api.vexa.ai/public/transcripts/abc123def456.txt",
          expires_at: "2024-01-01T13:00:00Z",
          expires_in_seconds: 3600,
        }}
        notes={[
          "Share links are public and do not require authentication",
          "Links expire after the specified TTL (default: 1 hour)",
          "Useful for sharing transcripts with AI tools that can read from URLs",
        ]}
        />
      </div>
    </div>
  );
}

