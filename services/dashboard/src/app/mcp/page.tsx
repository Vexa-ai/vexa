"use client";

import { useState, useEffect } from "react";
import { Copy, Check, Code, Settings } from "lucide-react";
import Image from "next/image";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { toast } from "sonner";

interface RuntimeConfig {
  wsUrl: string;
  apiUrl: string;
  publicApiUrl?: string;
  authToken: string | null;
}

export default function MCPPage() {
  const [config, setConfig] = useState<RuntimeConfig | null>(null);
  const [copied, setCopied] = useState(false);
  const [loading, setLoading] = useState(true);
  const [mcpIconError, setMcpIconError] = useState(false);

  useEffect(() => {
    async function fetchConfig() {
      try {
        const response = await fetch("/api/config");
        const data = await response.json();
        setConfig(data);
      } catch (error) {
        console.error("Failed to fetch config:", error);
        toast.error("Failed to load configuration");
      } finally {
        setLoading(false);
      }
    }
    fetchConfig();
  }, []);

  const getMCPUrl = () => {
    const base = config?.publicApiUrl || config?.apiUrl;
    if (!base) {
      return "https://api.cloud.vexa.ai/mcp";
    }
    return `${base.replace(/\/$/, "")}/mcp`;
  };

  const maskKey = (key: string): string => {
    if (key.length <= 12) return key;
    return `${key.slice(0, 8)}${"*".repeat(8)}${key.slice(-4)}`;
  };

  const buildMCPConfig = (masked: boolean): string => {
    const mcpUrl = getMCPUrl();
    const rawKey = config?.authToken || "YOUR_API_KEY_HERE";
    const apiKey = masked && config?.authToken ? maskKey(rawKey) : rawKey;

    return JSON.stringify({
      mcpServers: {
        Vexa: {
          command: "npx",
          args: [
            "-y",
            "mcp-remote",
            mcpUrl,
            "--header",
            "Authorization:${VEXA_API_KEY}",
          ],
          env: {
            VEXA_API_KEY: apiKey,
          },
        },
      },
    }, null, 2);
  };

  const displayConfig = () => buildMCPConfig(true);
  const copyableConfig = () => buildMCPConfig(false);

  const copyToClipboard = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      toast.success("Copied to clipboard");
      setTimeout(() => setCopied(false), 2000);
    } catch {
      toast.error("Failed to copy to clipboard");
    }
  };

  const handleCursorInstall = () => {
    if (!config?.authToken) {
      toast.error("No API token available. Create an API key in Profile first.");
      return;
    }

    const mcpUrl = getMCPUrl();
    const apiKey = config.authToken;

    const mcpServerConfig = {
      command: "npx",
      args: ["-y", "mcp-remote", mcpUrl, "--header", "Authorization:${VEXA_API_KEY}"],
      env: { VEXA_API_KEY: apiKey },
    };

    const fullMCPConfig = { mcpServers: { Vexa: mcpServerConfig } };
    const configJson = JSON.stringify(fullMCPConfig, null, 2);
    copyToClipboard(configJson);

    try {
      const configBase64 = btoa(JSON.stringify(mcpServerConfig));
      const configEncoded = encodeURIComponent(configBase64);
      const deepLink = `cursor://anysphere.cursor-deeplink/mcp/install?name=Vexa&config=${configEncoded}`;

      const link = document.createElement("a");
      link.href = deepLink;
      link.style.display = "none";
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);

      toast.success("Opening Cursor to install MCP server...", {
        description: "If Cursor doesn't open automatically, the config has been copied to your clipboard.",
        duration: 8000,
      });
    } catch {
      toast.info("Config copied to clipboard!", {
        description: "Please paste it into ~/.cursor/mcp.json and merge into existing mcpServers object if needed.",
        duration: 8000,
      });
    }
  };

  const handleVSCodeInstall = () => {
    if (!config?.authToken) {
      toast.error("No API token available. Create an API key in Profile first.");
      return;
    }

    const configJson = copyableConfig();
    copyToClipboard(configJson);

    const blob = new Blob([configJson], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "mcp.json";
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);

    toast.success("Config downloaded and copied!", {
      description: "Save the downloaded mcp.json to ~/.vscode/mcp.json (or merge into existing file).",
      duration: 10000,
    });
  };

  const MCPIcon = () => {
    if (mcpIconError) {
      return <Code className="h-5 w-5" />;
    }
    return (
      <div className="h-5 w-5 relative flex items-center justify-center">
        <img
          src="/icons/icons8-mcp-96 (1).png"
          alt="MCP"
          width={20}
          height={20}
          className="object-contain dark:invert"
          onError={() => setMcpIconError(true)}
        />
      </div>
    );
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-semibold tracking-[-0.02em] text-foreground">
          MCP Setup
        </h1>
        <p className="text-sm text-muted-foreground">
          Connect your AI coding assistant to Vexa via the Model Context Protocol
        </p>
      </div>

      {/* Quick Install */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <Card className="cursor-pointer hover:bg-muted/30 transition-colors" onClick={handleCursorInstall}>
          <CardContent className="pt-6 pb-6 flex items-center gap-4">
            <div className="h-10 w-10 rounded-lg bg-muted flex items-center justify-center">
              <Image src="/icons/cursor.svg" alt="Cursor" width={24} height={24} className="dark:invert" />
            </div>
            <div>
              <p className="font-medium">Connect to Cursor</p>
              <p className="text-xs text-muted-foreground">One-click install via deep link</p>
            </div>
          </CardContent>
        </Card>
        <Card className="cursor-pointer hover:bg-muted/30 transition-colors" onClick={handleVSCodeInstall}>
          <CardContent className="pt-6 pb-6 flex items-center gap-4">
            <div className="h-10 w-10 rounded-lg bg-muted flex items-center justify-center">
              <Image src="/icons/vscode.svg" alt="VS Code" width={24} height={24} />
            </div>
            <div>
              <p className="font-medium">Connect to VS Code</p>
              <p className="text-xs text-muted-foreground">Download mcp.json config file</p>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Manual Configuration */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Settings className="h-5 w-5" />
            Configuration
          </CardTitle>
          <CardDescription>
            Copy this JSON and add it to your editor&apos;s mcp.json file
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="relative">
            <Textarea
              value={loading ? "Loading..." : displayConfig()}
              readOnly
              className="font-mono text-sm min-h-[220px]"
            />
            <Button
              variant="outline"
              size="sm"
              className="absolute top-2 right-2"
              onClick={() => copyToClipboard(copyableConfig())}
              disabled={loading}
            >
              {copied ? (
                <Check className="h-4 w-4 text-green-500" />
              ) : (
                <Copy className="h-4 w-4" />
              )}
            </Button>
          </div>
          <div className="text-sm text-muted-foreground space-y-2 p-4 bg-muted rounded-lg">
            <p>
              <strong>Cursor:</strong>{" "}
              <code className="bg-background px-1.5 py-0.5 rounded text-xs">~/.cursor/mcp.json</code>
            </p>
            <p>
              <strong>VS Code:</strong>{" "}
              <code className="bg-background px-1.5 py-0.5 rounded text-xs">~/.vscode/mcp.json</code>
            </p>
            <p>
              <strong>Claude Code:</strong>{" "}
              <code className="bg-background px-1.5 py-0.5 rounded text-xs">~/.claude/mcp.json</code>
            </p>
            <p className="text-xs pt-2">
              If you already have an mcp.json file, merge the Vexa entry into the existing{" "}
              <code className="bg-background px-1.5 py-0.5 rounded text-xs">mcpServers</code> object.
            </p>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
