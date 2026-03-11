"use client";

import { useState, useEffect } from "react";
import { Copy, Check, Code, ExternalLink, ChevronDown, Settings } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
  DropdownMenuLabel,
} from "@/components/ui/dropdown-menu";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { toast } from "sonner";

interface RuntimeConfig {
  wsUrl: string;
  apiUrl: string;
  authToken: string | null;
}

export function MCPConfigButton() {
  const [config, setConfig] = useState<RuntimeConfig | null>(null);
  const [showDialog, setShowDialog] = useState(false);
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

  // Determine MCP URL from the API URL
  // The MCP endpoint is at /mcp path on the API server
  const getMCPUrl = () => {
    if (!config?.apiUrl) {
      // Fallback to cloud if no API URL is configured
      return "https://api.cloud.vexa.ai/mcp";
    }
    
    // Derive MCP URL from API URL
    // Remove trailing slash if present, then append /mcp
    const baseUrl = config.apiUrl.replace(/\/$/, "");
    return `${baseUrl}/mcp`;
  };

  const generateMCPConfig = (): string => {
    if (!config?.authToken) {
      return JSON.stringify({
        mcpServers: {
          Vexa: {
            command: "npx",
            args: [
              "-y",
              "mcp-remote",
              getMCPUrl(),
              "--header",
              "Authorization:${VEXA_API_KEY}",
            ],
            env: {
              VEXA_API_KEY: "YOUR_API_KEY_HERE",
            },
          },
        },
      }, null, 2);
    }

    return JSON.stringify({
      mcpServers: {
        Vexa: {
          command: "npx",
          args: [
            "-y",
            "mcp-remote",
            getMCPUrl(),
            "--header",
            "Authorization:${VEXA_API_KEY}",
          ],
          env: {
            VEXA_API_KEY: config.authToken,
          },
        },
      },
    }, null, 2);
  };

  const copyToClipboard = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      toast.success("Copied to clipboard");
      setTimeout(() => setCopied(false), 2000);
    } catch (error) {
      toast.error("Failed to copy to clipboard");
    }
  };

  const handleCopyConfig = () => {
    const configJson = generateMCPConfig();
    copyToClipboard(configJson);
  };

  const handleShowConfig = () => {
    setShowDialog(true);
  };

  const getConfigFilePath = (editor: "cursor" | "vscode") => {
    const isWindows = typeof window !== "undefined" && navigator.platform.includes("Win");
    
    if (editor === "cursor") {
      if (isWindows) {
        return "%APPDATA%\\Cursor\\mcp.json";
      }
      return "~/.cursor/mcp.json";
    } else {
      if (isWindows) {
        return "%APPDATA%\\Code\\User\\mcp.json";
      }
      return "~/.vscode/mcp.json";
    }
  };

  const handleCursorInstall = () => {
    if (!config?.authToken) {
      toast.error("No API token available");
      return;
    }

    const mcpUrl = getMCPUrl();
    const apiKey = config.authToken;
    
    // Create the MCP server configuration matching the format in mcp.json
    // This is the server config object (without the "Vexa" key wrapper)
    const mcpServerConfig = {
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
    };

    // Create the full MCP configuration JSON for manual installation fallback
    // This wraps it in mcpServers with the "Vexa" key for the complete mcp.json file
    const fullMCPConfig = {
      mcpServers: {
        Vexa: mcpServerConfig,
      },
    };

    const configJson = JSON.stringify(fullMCPConfig, null, 2);
    
    // Copy full config to clipboard as fallback
    copyToClipboard(configJson);
    
    // Create Cursor deep link using the format from Tadata:
    // cursor://anysphere.cursor-deeplink/mcp/install?name=<name>&config=<base64-encoded-config>
    // The config should be just the server config object (without the "Vexa" key)
    // Cursor will add the "Vexa" key based on the name parameter
    try {
      // Base64 encode the MCP server configuration (just the config object, not wrapped)
      const configBase64 = btoa(JSON.stringify(mcpServerConfig));
      const configEncoded = encodeURIComponent(configBase64);
      
      // Create the deep link
      const deepLink = `cursor://anysphere.cursor-deeplink/mcp/install?name=Vexa&config=${configEncoded}`;
      
      // Try to open the deep link using an anchor element
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
    } catch (error) {
      // Fallback: just copy config
      const filePath = getConfigFilePath("cursor");
      toast.info("Config copied to clipboard!", {
        description: `Please paste it into ${filePath} and merge into existing mcpServers object if needed.`,
        duration: 8000,
      });
    }
  };

  const handleVSCodeInstall = () => {
    if (!config?.authToken) {
      toast.error("No API token available");
      return;
    }

    const mcpUrl = getMCPUrl();
    const apiKey = config.authToken;
    
    // Create the full MCP configuration
    const fullMCPConfig = {
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
    };

    const configJson = JSON.stringify(fullMCPConfig, null, 2);
    
    // Copy to clipboard
    copyToClipboard(configJson);
    
    // Create a downloadable file
    const blob = new Blob([configJson], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "mcp.json";
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
    
    const filePath = getConfigFilePath("vscode");
    toast.success("Config downloaded and copied!", {
      description: `Save the downloaded mcp.json file to ${filePath} (or merge into existing file). Config is also in your clipboard.`,
      duration: 10000,
    });
  };

  // MCP Icon component with fallback
  const MCPIcon = () => {
    if (mcpIconError) {
      return <Code className="mr-2 h-4 w-4" />;
    }
    return (
      <div className="mr-2 h-4 w-4 relative flex items-center justify-center">
        <img
          src="/icons/icons8-mcp-96 (1).png"
          alt="MCP"
          width={16}
          height={16}
          className="object-contain dark:invert"
          onError={() => setMcpIconError(true)}
        />
      </div>
    );
  };

  if (loading) {
    return (
      <Button variant="outline" disabled>
        <MCPIcon />
        MCP Config
      </Button>
    );
  }

  if (!config?.authToken) {
    return (
      <Button variant="outline" disabled>
        <MCPIcon />
        MCP Config
      </Button>
    );
  }

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="outline">
            <MCPIcon />
            Setup MCP
            <ChevronDown className="ml-2 h-4 w-4" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-64">
          <DropdownMenuLabel>MCP Server Configuration</DropdownMenuLabel>
          <DropdownMenuSeparator />
          <DropdownMenuItem onClick={handleShowConfig}>
            <Settings className="mr-2 h-4 w-4" />
            <div className="flex flex-col">
              <span>View Config</span>
              <span className="text-xs text-muted-foreground">View configuration JSON</span>
            </div>
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem onClick={handleCursorInstall}>
            <ExternalLink className="mr-2 h-4 w-4" />
            <div className="flex flex-col">
              <span>Connect to Cursor</span>
              <span className="text-xs text-muted-foreground">Install MCP Server on Cursor</span>
            </div>
          </DropdownMenuItem>
          <DropdownMenuItem onClick={handleVSCodeInstall}>
            <ExternalLink className="mr-2 h-4 w-4" />
            <div className="flex flex-col">
              <span>Connect to VS Code</span>
              <span className="text-xs text-muted-foreground">Install MCP Server on VS Code</span>
            </div>
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <Dialog open={showDialog} onOpenChange={setShowDialog}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>MCP Server Configuration</DialogTitle>
            <DialogDescription>
              Copy this configuration and add it to your mcp.json file
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="relative">
              <Textarea
                value={generateMCPConfig()}
                readOnly
                className="font-mono text-sm min-h-[300px]"
              />
              <Button
                variant="outline"
                size="sm"
                className="absolute top-2 right-2"
                onClick={() => copyToClipboard(generateMCPConfig())}
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
                <strong>For Cursor:</strong> Edit{" "}
                <code className="bg-background px-1.5 py-0.5 rounded">~/.cursor/mcp.json</code> (macOS/Linux) or{" "}
                <code className="bg-background px-1.5 py-0.5 rounded">%APPDATA%\\Cursor\\mcp.json</code> (Windows)
              </p>
              <p>
                <strong>For VS Code:</strong> Edit{" "}
                <code className="bg-background px-1.5 py-0.5 rounded">~/.vscode/mcp.json</code> (macOS/Linux) or{" "}
                <code className="bg-background px-1.5 py-0.5 rounded">%APPDATA%\\Code\\User\\mcp.json</code> (Windows)
              </p>
              <p className="text-xs pt-2">
                If you already have an mcp.json file, merge the Vexa configuration into the existing{" "}
                <code className="bg-background px-1.5 py-0.5 rounded">mcpServers</code> object.
              </p>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
