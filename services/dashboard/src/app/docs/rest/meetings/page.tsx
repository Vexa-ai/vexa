  import { Metadata } from "next";
import { APIEndpointDoc } from "@/components/docs/api-endpoint-doc";

export const metadata: Metadata = {
  title: "Meetings API | Vexa API Documentation",
  description: "API reference for managing meetings and meeting metadata",
};

export default function MeetingsPage() {
  return (
    <div className="space-y-12">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Meetings API</h1>
        <p className="text-muted-foreground mt-2">
          Manage meetings, update metadata, and track meeting status.
        </p>
      </div>

      {/* List Meetings */}
      <div id="list-meetings">
        <APIEndpointDoc
          title="List Meetings"
        description="Get a list of all meetings for the authenticated user."
        method="GET"
        path="/meetings"
        authType="user"
        dashboardProxy="/api/vexa/meetings"
        responseExample={{
          meetings: [
            {
              id: 123,
              platform: "google_meet",
              native_meeting_id: "abc-defg-hij",
              status: "completed",
              start_time: "2024-01-01T12:00:00Z",
              end_time: "2024-01-01T13:00:00Z",
              bot_container_id: null,
              data: {
                name: "Team Standup",
                participants: ["Alice", "Bob"],
              },
              created_at: "2024-01-01T12:00:00Z",
            },
          ],
        }}
        notes={[
          "Returns meetings sorted by creation date (newest first)",
          "Includes meetings in all statuses: requested, joining, active, completed, failed",
        ]}
        />
      </div>

      {/* Get Meeting */}
      <div id="get-meeting">
        <APIEndpointDoc
          title="Get Meeting"
          description="Get detailed information about a specific meeting."
          method="GET"
          path="/meetings/{id}"
          authType="user"
          dashboardProxy="/api/vexa/meetings/{id}"
          pathParams={[
            {
              name: "id",
              type: "integer",
              description: "The meeting ID",
              required: true,
            },
          ]}
          responseExample={{
            id: 123,
            platform: "google_meet",
            native_meeting_id: "abc-defg-hij",
            status: "active",
            start_time: "2024-01-01T12:00:00Z",
            end_time: null,
            bot_container_id: "container-123",
            data: {
              name: "Team Standup",
              participants: ["Alice", "Bob"],
              languages: ["en"],
            },
            created_at: "2024-01-01T12:00:00Z",
          }}
          errorExamples={[
            {
              status: 404,
              body: { error: "Meeting not found" },
              description: "The specified meeting does not exist",
            },
          ]}
        />
      </div>

      {/* Update Meeting Data */}
      <div id="update-meeting-data">
        <APIEndpointDoc
          title="Update Meeting Data"
        description="Update meeting metadata such as name, notes, participants, and languages."
        method="PATCH"
        path="/meetings/{platform}/{native_meeting_id}"
        authType="user"
        dashboardProxy="/api/vexa/meetings/{platform}/{native_meeting_id}"
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
          description: "Meeting data updates",
          schema: {
            data: {
              name: "Updated Meeting Name",
              notes: "Meeting notes here",
              participants: ["Alice", "Bob", "Charlie"],
              languages: ["en", "es"],
            },
          },
        }}
        responseExample={{
          id: 123,
          platform: "google_meet",
          native_meeting_id: "abc-defg-hij",
          status: "active",
          data: {
            name: "Updated Meeting Name",
            notes: "Meeting notes here",
            participants: ["Alice", "Bob", "Charlie"],
            languages: ["en", "es"],
          },
        }}
        notes={[
          "All fields in the data object are optional",
          "Only provided fields will be updated",
          "Useful for adding context and metadata to meetings",
        ]}
        />
      </div>
    </div>
  );
}

