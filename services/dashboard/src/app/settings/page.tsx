"use client";

import { useState, useEffect } from "react";
import { Settings, CheckCircle2, XCircle, Loader2, ExternalLink, Sparkles, AlertCircle } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Badge } from "@/components/ui/badge";
import { toast } from "sonner";
import { vexaAPI } from "@/lib/api";
import { AdminGuard } from "@/components/admin/admin-guard";

interface AIConfig {
  enabled: boolean;
  provider: string | null;
  model: string | null;
  hasApiKey?: boolean;
  hasBaseUrl?: boolean;
}

interface RuntimeConfig {
  wsUrl: string;
  apiUrl: string;
}

function SettingsContent() {
  const [isTesting, setIsTesting] = useState(false);
  const [connectionStatus, setConnectionStatus] = useState<"unknown" | "connected" | "error">("unknown");
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const [aiConfig, setAIConfig] = useState<AIConfig | null>(null);
  const [isLoadingAIConfig, setIsLoadingAIConfig] = useState(true);
  const [runtimeConfig, setRuntimeConfig] = useState<RuntimeConfig | null>(null);

  // Fetch configurations on mount
  useEffect(() => {
    async function fetchConfigs() {
      // Fetch runtime config (WebSocket URL)
      try {
        const configResponse = await fetch("/api/config");
        const config = await configResponse.json();
        setRuntimeConfig(config);
      } catch (error) {
        console.error("Failed to fetch runtime config:", error);
      }

      // Fetch AI config
      try {
        const response = await fetch("/api/ai/config");
        const config = await response.json();
        setAIConfig(config);
      } catch (error) {
        console.error("Failed to fetch AI config:", error);
        setAIConfig({ enabled: false, provider: null, model: null });
      } finally {
        setIsLoadingAIConfig(false);
      }
    }
    fetchConfigs();
  }, []);

  const handleTestConnection = async () => {
    setIsTesting(true);
    setConnectionStatus("unknown");
    setConnectionError(null);

    try {
      const result = await vexaAPI.testConnection();
      if (result.success) {
        setConnectionStatus("connected");
        toast.success("Connection successful", {
          description: "Successfully connected to Vexa API",
        });
      } else {
        setConnectionStatus("error");
        setConnectionError(result.error || "Unknown error");
        toast.error("Connection failed", {
          description: result.error,
        });
      }
    } catch (error) {
      setConnectionStatus("error");
      setConnectionError((error as Error).message);
      toast.error("Connection failed", {
        description: (error as Error).message,
      });
    } finally {
      setIsTesting(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-semibold tracking-[-0.02em] text-foreground">Settings</h1>
        <p className="text-sm text-muted-foreground">
          Configure your Vexa Dashboard connection
        </p>
      </div>

      <div className="max-w-2xl space-y-6">
        {/* API Configuration */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Settings className="h-5 w-5" />
              Vexa API Configuration
            </CardTitle>
            <CardDescription>
              Configure the connection to your Vexa instance. These settings are managed via environment variables.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-6">
            {/* API URL */}
            <div className="space-y-2">
              <Label htmlFor="apiUrl">API URL</Label>
              <Input
                id="apiUrl"
                value={runtimeConfig?.apiUrl || "Loading..."}
                disabled
                className="font-mono bg-muted"
              />
              <p className="text-xs text-muted-foreground">
                Set via <code className="bg-muted px-1 rounded">VEXA_API_URL</code> environment variable
              </p>
            </div>

            {/* WebSocket URL */}
            <div className="space-y-2">
              <Label htmlFor="wsUrl">WebSocket URL</Label>
              <Input
                id="wsUrl"
                value={runtimeConfig?.wsUrl || "Loading..."}
                disabled
                className="font-mono bg-muted"
              />
              <p className="text-xs text-muted-foreground">
                Auto-derived from <code className="bg-muted px-1 rounded">VEXA_API_URL</code>
              </p>
            </div>

            {/* Admin API Key Status */}
            <div className="space-y-2">
              <Label>Admin API Key</Label>
              <div className="flex items-center gap-2">
                <Input
                  value="••••••••••••••••••••••••••••••••"
                  disabled
                  className="font-mono bg-muted"
                />
                <Badge variant="secondary">Configured</Badge>
              </div>
              <p className="text-xs text-muted-foreground">
                Set via <code className="bg-muted px-1 rounded">VEXA_ADMIN_API_KEY</code> environment variable
              </p>
            </div>

            <Separator />

            {/* Test Connection */}
            <div className="flex items-center justify-between">
              <div className="space-y-1">
                <p className="font-medium">Connection Status</p>
                <div className="flex items-center gap-2">
                  {connectionStatus === "connected" && (
                    <>
                      <CheckCircle2 className="h-4 w-4 text-green-500" />
                      <span className="text-sm text-green-600">Connected</span>
                    </>
                  )}
                  {connectionStatus === "error" && (
                    <>
                      <XCircle className="h-4 w-4 text-red-500" />
                      <span className="text-sm text-red-600">
                        {connectionError || "Connection failed"}
                      </span>
                    </>
                  )}
                  {connectionStatus === "unknown" && (
                    <span className="text-sm text-muted-foreground">Not tested</span>
                  )}
                </div>
              </div>
              <Button onClick={handleTestConnection} disabled={isTesting}>
                {isTesting ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    Testing...
                  </>
                ) : (
                  "Test Connection"
                )}
              </Button>
            </div>
          </CardContent>
        </Card>

        {/* AI Configuration */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Sparkles className="h-5 w-5" />
              AI Assistant Configuration
            </CardTitle>
            <CardDescription>
              AI settings for meeting transcript analysis. Configure via environment variables.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-6">
            {isLoadingAIConfig ? (
              <div className="flex items-center gap-2 text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
                <span>Checking AI configuration...</span>
              </div>
            ) : aiConfig?.enabled ? (
              <>
                {/* Status */}
                <div className="flex items-center gap-2">
                  <CheckCircle2 className="h-5 w-5 text-green-500" />
                  <span className="font-medium text-green-600">AI Assistant Enabled</span>
                </div>

                {/* Provider */}
                <div className="space-y-2">
                  <Label>Provider</Label>
                  <Input
                    value={aiConfig.provider || "Unknown"}
                    disabled
                    className="font-mono bg-muted capitalize"
                  />
                </div>

                {/* Model */}
                <div className="space-y-2">
                  <Label>Model</Label>
                  <Input
                    value={aiConfig.model || "Unknown"}
                    disabled
                    className="font-mono bg-muted"
                  />
                </div>

                {/* API Key Status */}
                <div className="space-y-2">
                  <Label>API Key</Label>
                  <div className="flex items-center gap-2">
                    <Input
                      value="••••••••••••••••••••••••••••••••"
                      disabled
                      className="font-mono bg-muted"
                    />
                    <Badge variant={aiConfig.hasApiKey ? "secondary" : "destructive"}>
                      {aiConfig.hasApiKey ? "Configured" : "Missing"}
                    </Badge>
                  </div>
                </div>

                {/* Base URL (if set) */}
                {aiConfig.hasBaseUrl && (
                  <div className="space-y-2">
                    <Label>Custom Base URL</Label>
                    <div className="flex items-center gap-2">
                      <Input
                        value="Custom endpoint configured"
                        disabled
                        className="bg-muted"
                      />
                      <Badge variant="secondary">Set</Badge>
                    </div>
                  </div>
                )}
              </>
            ) : (
              <div className="flex flex-col items-center justify-center py-8 text-center">
                <div className="h-12 w-12 rounded-full bg-muted flex items-center justify-center mb-4">
                  <AlertCircle className="h-6 w-6 text-muted-foreground" />
                </div>
                <h3 className="font-medium mb-1">AI Not Configured</h3>
                <p className="text-sm text-muted-foreground max-w-sm">
                  Set <code className="bg-muted px-1 rounded">AI_MODEL</code> and{" "}
                  <code className="bg-muted px-1 rounded">AI_API_KEY</code> environment variables to enable AI features.
                </p>
              </div>
            )}
          </CardContent>
        </Card>

        {/* Environment Variables */}
        <Card>
          <CardHeader>
            <CardTitle>Environment Variables</CardTitle>
            <CardDescription>
              To configure the dashboard, create a <code className="bg-muted px-1 rounded">.env.local</code> file with these variables
            </CardDescription>
          </CardHeader>
          <CardContent>
            <pre className="bg-muted p-4 rounded-lg text-sm overflow-x-auto">
{`# Vexa API Configuration (required)
VEXA_API_URL=http://localhost:18056
VEXA_ADMIN_API_KEY=your_admin_api_key_here

# AI Assistant Configuration (optional)
# Format: provider/model
AI_MODEL=openai/gpt-4o
AI_API_KEY=your_ai_api_key_here`}
            </pre>
          </CardContent>
        </Card>

        {/* About */}
        <Card>
          <CardHeader>
            <CardTitle>About</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Version</span>
              <span className="font-medium">1.0.0</span>
            </div>
            <Separator />
            <div className="space-y-2">
              <p className="text-sm text-muted-foreground">
                Vexa Dashboard is an open source web interface for Vexa, the self-hosted meeting transcription API.
              </p>
              <div className="flex gap-4">
                <a
                  href="https://github.com/Vexa-ai/vexa"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-sm text-primary hover:underline inline-flex items-center gap-1"
                >
                  Vexa GitHub
                  <ExternalLink className="h-3 w-3" />
                </a>
                <a
                  href="https://vexa.ai"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-sm text-primary hover:underline inline-flex items-center gap-1"
                >
                  Vexa Website
                  <ExternalLink className="h-3 w-3" />
                </a>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

export default function SettingsPage() {
  return (
    <AdminGuard>
      <SettingsContent />
    </AdminGuard>
  );
}
