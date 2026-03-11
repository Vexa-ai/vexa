import { Metadata } from "next";
import { APIEndpointDoc } from "@/components/docs/api-endpoint-doc";

export const metadata: Metadata = {
  title: "Bots API | Vexa API Documentation",
  description: "API reference for managing transcription bots",
};

export default function BotsPage() {
  return (
    <div className="space-y-12">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Bots API</h1>
        <p className="text-muted-foreground mt-2">
          Manage transcription bots that join meetings and transcribe audio in real-time.
        </p>
      </div>

      {/* Create Bot */}
      <div id="create-bot">
        <APIEndpointDoc
          title="Create Bot"
        description="Send a transcription bot to join a meeting. The bot will automatically join and start transcribing."
        method="POST"
        path="/bots"
        authType="user"
        dashboardProxy="/api/vexa/bots"
        requestBody={{
          description: "Bot configuration",
          schema: {
            platform: "google_meet",
            native_meeting_id: "abc-defg-hij",
            passcode: "optional_passcode",
            bot_name: "Vexa Transcription Bot",
            language: "en",
          },
        }}
        responseExample={{
          id: 123,
          platform: "google_meet",
          native_meeting_id: "abc-defg-hij",
          status: "requested",
          start_time: null,
          end_time: null,
          bot_container_id: null,
          data: {},
          created_at: "2024-01-01T12:00:00Z",
        }}
        errorExamples={[
          {
            status: 400,
            body: { error: "Invalid meeting ID format" },
            description: "The meeting ID format is invalid for the specified platform",
          },
          {
            status: 401,
            body: { error: "Unauthorized" },
            description: "Invalid or missing API key",
          },
          {
            status: 429,
            body: { error: "Too many concurrent bots" },
            description: "User has reached their maximum concurrent bot limit",
          },
        ]}
        notes={[
          "The bot will transition through statuses: requested → joining → awaiting_admission → active",
          "For Google Meet, meeting IDs follow the format: abc-defg-hij",
          "For Microsoft Teams, meeting IDs are numeric and require a passcode",
          "The language parameter is optional. If not specified, the bot will auto-detect the language",
        ]}
        />
      </div>

      {/* Stop Bot */}
      <div id="stop-bot">
        <APIEndpointDoc
          title="Stop Bot"
        description="Stop an active transcription bot and disconnect it from the meeting."
        method="DELETE"
        path="/bots/{platform}/{native_meeting_id}"
        authType="user"
        dashboardProxy="/api/vexa/bots/{platform}/{native_meeting_id}"
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
        responseExample={null}
        errorExamples={[
          {
            status: 404,
            body: { error: "Bot not found" },
            description: "No active bot found for the specified meeting",
          },
        ]}
        notes={[
          "Stopping a bot will mark the meeting as completed",
          "Transcripts remain available after stopping the bot",
        ]}
        />
      </div>

      {/* Update Bot Config */}
      <div id="update-bot-configuration">
        <APIEndpointDoc
          title="Update Bot Configuration"
        description="Update the configuration of an active bot, such as changing the transcription language."
        method="PUT"
        path="/bots/{platform}/{native_meeting_id}/config"
        authType="user"
        dashboardProxy="/api/vexa/bots/{platform}/{native_meeting_id}/config"
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
        requestBody={{
          description: "Configuration updates",
          schema: {
            language: "es",
            task: "transcribe",
            bot_name: "Updated Bot Name",
          },
        }}
        responseExample={null}
        errorExamples={[
          {
            status: 404,
            body: { error: "Bot not found" },
            description: "No active bot found for the specified meeting",
          },
        ]}
        notes={[
          "Only active bots can have their configuration updated",
          "Language changes take effect immediately for new transcript segments",
        ]}
        />
      </div>

      {/* Get Bot Status */}
      <div id="get-bot-status">
        <APIEndpointDoc
          title="Get Bot Status"
          description="Get the status of all currently running bots."
          method="GET"
          path="/bots/status"
          authType="user"
          dashboardProxy="/api/vexa/bots/status"
          responseExample={{
            running_bots: [
              {
                container_id: "abc123",
                meeting_id: 123,
                platform: "google_meet",
                native_meeting_id: "abc-defg-hij",
              },
            ],
          }}
          notes={[
            "Returns only bots that are currently running (not completed or failed)",
            "Useful for monitoring active transcription sessions",
          ]}
        />
      </div>
    </div>
  );
}

