import { Metadata } from "next";
import { APIEndpointDoc } from "@/components/docs/api-endpoint-doc";

export const metadata: Metadata = {
  title: "Admin Users API | Vexa API Documentation",
  description: "API reference for managing users via Admin API",
};

export default function AdminUsersPage() {
  return (
    <div className="space-y-12">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Admin Users API</h1>
        <p className="text-muted-foreground mt-2">
          Manage users and their API tokens. Requires Admin API key authentication.
        </p>
      </div>

      {/* List Users */}
      <div id="list-users">
        <APIEndpointDoc
          title="List Users"
          description="Get a list of all users in the system."
          method="GET"
          path="/admin/users"
          authType="admin"
          dashboardProxy="/api/admin/users"
          queryParams={[
            {
              name: "skip",
              type: "integer",
              description: "Number of users to skip (pagination)",
              required: false,
            },
            {
              name: "limit",
              type: "integer",
              description: "Maximum number of users to return",
              required: false,
            },
          ]}
          responseExample={[
            {
              id: "user-123",
              email: "user@example.com",
              name: "John Doe",
              max_concurrent_bots: 3,
              created_at: "2024-01-01T12:00:00Z",
            },
          ]}
        />
      </div>

      {/* Get User */}
      <div id="get-user">
        <APIEndpointDoc
          title="Get User"
        description="Get detailed information about a specific user, including their API tokens."
        method="GET"
        path="/admin/users/{id}"
        authType="admin"
        dashboardProxy="/api/admin/users/{id}"
        pathParams={[
          {
            name: "id",
            type: "string",
            description: "The user ID",
            required: true,
          },
        ]}
        responseExample={{
          id: "user-123",
          email: "user@example.com",
          name: "John Doe",
          max_concurrent_bots: 3,
          api_tokens: [
            {
              id: "token-123",
              token: "***", // Token value is only shown at creation
              user_id: "user-123",
              created_at: "2024-01-01T12:00:00Z",
            },
          ],
          created_at: "2024-01-01T12:00:00Z",
        }}
        />
      </div>

      {/* Create User */}
      <div id="create-user">
        <APIEndpointDoc
          title="Create User"
        description="Create a new user in the system."
        method="POST"
        path="/admin/users"
        authType="admin"
        dashboardProxy="/api/admin/users"
        requestBody={{
          schema: {
            email: "user@example.com",
            name: "John Doe",
            max_concurrent_bots: 3,
          },
        }}
        responseExample={{
          id: "user-123",
          email: "user@example.com",
          name: "John Doe",
          max_concurrent_bots: 3,
          created_at: "2024-01-01T12:00:00Z",
        }}
        />
      </div>

      {/* Create Token */}
      <div id="create-api-token">
        <APIEndpointDoc
          title="Create API Token"
        description="Create a new API token for a user. The token value is only shown once at creation."
        method="POST"
        path="/admin/users/{id}/tokens"
        authType="admin"
        dashboardProxy="/api/admin/users/{id}/tokens"
        pathParams={[
          {
            name: "id",
            type: "string",
            description: "The user ID",
            required: true,
          },
        ]}
        responseExample={{
          id: "token-123",
          token: "vex_abc123def456...", // Only shown once!
          user_id: "user-123",
          created_at: "2024-01-01T12:00:00Z",
        }}
        notes={[
          "⚠️ IMPORTANT: Save the token immediately. It cannot be retrieved later.",
          "Each user can have multiple API tokens",
          "Tokens do not expire but can be revoked",
        ]}
        />
      </div>
    </div>
  );
}

