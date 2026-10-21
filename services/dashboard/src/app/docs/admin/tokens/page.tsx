import { Metadata } from "next";
import { APIEndpointDoc } from "@/components/docs/api-endpoint-doc";

export const metadata: Metadata = {
  title: "Admin Tokens API | Vexa API Documentation",
  description: "API reference for managing API tokens via Admin API",
};

export default function AdminTokensPage() {
  return (
    <div className="space-y-12">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Admin Tokens API</h1>
        <p className="text-muted-foreground mt-2">
          Manage API tokens for users. Requires Admin API key authentication.
        </p>
      </div>

      {/* Revoke Token */}
      <div id="revoke-token">
        <APIEndpointDoc
          title="Revoke Token"
        description="Revoke an API token, preventing it from being used for authentication."
        method="DELETE"
        path="/admin/tokens/{token_id}"
        authType="admin"
        dashboardProxy="/api/admin/tokens/{token_id}"
        pathParams={[
          {
            name: "token_id",
            type: "string",
            description: "The token ID to revoke",
            required: true,
          },
        ]}
        responseExample={null}
        notes={[
          "Revoked tokens cannot be restored",
          "Users will need to create a new token if they lose access",
        ]}
        />
      </div>
    </div>
  );
}

