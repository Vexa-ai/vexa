import { Metadata } from "next";
import { APIEndpointDoc } from "@/components/docs/api-endpoint-doc";

export const metadata: Metadata = {
  title: "Authentication | Vexa API Documentation",
  description: "Learn how to authenticate API requests to Vexa using API keys",
};

export default function AuthPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Authentication</h1>
        <p className="text-muted-foreground mt-2">
          Vexa uses API keys for authentication. There are two types of API keys: User API keys and Admin API keys.
        </p>
      </div>

      <div className="space-y-6">
        <div className="prose prose-sm max-w-none">
          <h2>User API Keys</h2>
          <p>
            User API keys grant access to meeting operations: creating bots, fetching transcripts, and managing meetings.
            These keys are scoped to individual users and can be created through the Admin API or dashboard.
          </p>
          <p>
            Include your User API key in the <code>X-API-Key</code> header for all REST API requests:
          </p>
          <pre className="bg-muted p-4 rounded-lg">
            <code>X-API-Key: your_user_api_key_here</code>
          </pre>
        </div>

        <div className="prose prose-sm max-w-none">
          <h2>Admin API Keys</h2>
          <p>
            Admin API keys provide full system access, including user management and token creation.
            These keys are configured at the server level and should be kept secure.
          </p>
          <p>
            Include your Admin API key in the <code>X-Admin-API-Key</code> header for Admin API requests:
          </p>
          <pre className="bg-muted p-4 rounded-lg">
            <code>X-Admin-API-Key: your_admin_api_key_here</code>
          </pre>
        </div>

        <div className="prose prose-sm max-w-none">
          <h2>WebSocket Authentication</h2>
          <p>
            WebSocket connections authenticate using the same User API key, but passed as a query parameter
            (since browsers cannot set custom headers for WebSocket connections):
          </p>
          <pre className="bg-muted p-4 rounded-lg">
            <code>wss://api.vexa.ai/ws?api_key=your_user_api_key_here</code>
          </pre>
        </div>

        <div className="prose prose-sm max-w-none">
          <h2>Getting Your API Key</h2>
          <p>
            To get a User API key:
          </p>
          <ol>
            <li>Log in to the Vexa Dashboard</li>
            <li>Go to Settings or use the Admin API to create a token</li>
            <li>Copy your API key and store it securely</li>
          </ol>
          <p className="text-sm text-muted-foreground mt-4">
            <strong>Note:</strong> API keys are only shown once at creation. If you lose your key, you'll need to create a new one.
          </p>
        </div>
      </div>
    </div>
  );
}

