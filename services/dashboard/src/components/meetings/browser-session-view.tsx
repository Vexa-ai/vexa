"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import {
  ArrowLeft,
  Monitor,
  Save,
  ExternalLink,
  Trash2,
  PanelRightOpen,
  PanelRightClose,
  Loader2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { toast } from "sonner";
import type { Meeting } from "@/types/vexa";
import { withBasePath } from "@/lib/base-path";

function CopyBlock({ label, text }: { label: string; text: string }) {
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-muted-foreground">{label}</span>
        <button
          className="text-xs text-primary hover:underline"
          onClick={() => {
            navigator.clipboard.writeText(text);
            toast.success(`${label} copied`);
          }}
        >
          Copy
        </button>
      </div>
      <pre
        className="p-2 bg-muted rounded text-xs font-mono whitespace-pre-wrap break-all cursor-pointer hover:bg-muted/80"
        onClick={() => {
          navigator.clipboard.writeText(text);
          toast.success(`${label} copied`);
        }}
      >
        {text}
      </pre>
    </div>
  );
}

interface BrowserSessionViewProps {
  meeting: Meeting;
}

export function BrowserSessionView({ meeting }: BrowserSessionViewProps) {
  const router = useRouter();
  const [apiUrl, setApiUrl] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const [isStopping, setIsStopping] = useState(false);
  const [showPanel, setShowPanel] = useState(false);

  useEffect(() => {
    fetch(withBasePath("/api/config"))
      .then((r) => r.json())
      .then((cfg) => {
        setApiUrl(cfg.publicApiUrl || cfg.apiUrl || "http://localhost:8056");
      });
  }, []);

  const token = meeting.data?.session_token as string | undefined;
  const isActive = meeting.status === "active" || meeting.status === "joining";

  // Session ended / no token
  if (!token || !isActive) {
    return (
      <div className="flex flex-col items-center justify-center h-[calc(100vh-64px)] gap-4">
        <Monitor className="h-16 w-16 text-muted-foreground" />
        <h2 className="text-xl font-semibold">Session ended</h2>
        <p className="text-sm text-muted-foreground">
          This browser session has been completed.
        </p>
        <Button variant="outline" asChild>
          <Link href="/meetings">
            <ArrowLeft className="h-4 w-4 mr-2" />
            Back to Meetings
          </Link>
        </Button>
      </div>
    );
  }

  // VNC/CDP use relative URLs — nginx proxies /b/ routes to the gateway (same origin, no CORS)
  const vncUrl = `/b/${token}/vnc/vnc.html?autoconnect=true&resize=scale&reconnect=true&path=b/${token}/vnc/websockify`;
  const cdpUrl = apiUrl ? `${apiUrl}/b/${token}/cdp` : `/b/${token}/cdp`;
  const mcpUrl = apiUrl ? `${apiUrl}/mcp` : null;
  const sshPort = meeting.data?.ssh_port as number | undefined;
  const sshHost = apiUrl ? (() => { try { return new URL(apiUrl).hostname; } catch { return "localhost"; } })() : "localhost";

  const agentInstructions = cdpUrl
    ? [
        `You have access to a remote browser session. The user can see everything you do live via VNC.`,
        ``,
        `Browser control (Playwright CDP):`,
        `  const browser = await chromium.connectOverCDP('${cdpUrl}');`,
        `  const page = browser.contexts()[0].pages()[0];`,
        `  // goto, click, fill, screenshot, evaluate, waitForSelector, etc.`,
        ``,
        ...(sshPort
          ? [
              `Shell access (SSH into the container):`,
              `  ssh root@${sshHost} -p ${sshPort}`,
              `  Password: ${token}`,
              `  Workspace: /workspace`,
              ``,
            ]
          : []),
        `The browser is a full Chromium instance. The user sees your actions in real time.`,
      ].join("\n")
    : "";

  const [isDeleting, setIsDeleting] = useState(false);

  async function handleSave() {
    setIsSaving(true);
    try {
      const response = await fetch(withBasePath(`/b/${token}/save`), {
        method: "POST",
      });
      if (!response.ok) throw new Error(await response.text());
      toast.success("Storage saved");
    } catch (error) {
      toast.error("Save failed: " + (error as Error).message);
    } finally {
      setIsSaving(false);
    }
  }

  async function handleDeleteStorage() {
    if (!confirm("Delete all stored browser data? You will need to log in again.")) return;
    setIsDeleting(true);
    try {
      const response = await fetch(withBasePath(`/b/${token}/storage`), {
        method: "DELETE",
      });
      if (!response.ok) throw new Error(await response.text());
      toast.success("Storage deleted");
    } catch (error) {
      toast.error("Delete failed: " + (error as Error).message);
    } finally {
      setIsDeleting(false);
    }
  }

  async function handleStop() {
    setIsStopping(true);
    try {
      const response = await fetch(withBasePath(`/api/vexa/bots/browser_session/${meeting.platform_specific_id}`), {
        method: "DELETE",
      });
      if (!response.ok) {
        const err = await response.json().catch(() => ({ detail: "Failed" }));
        throw new Error(err.detail || `Stop failed (${response.status})`);
      }
      toast.success("Browser session stopped");
      router.push("/meetings");
    } catch (error) {
      toast.error("Failed to stop session: " + (error as Error).message);
      setIsStopping(false);
    }
  }

  return (
    <div className="flex flex-col h-[calc(100vh-64px)]">
      {/* Toolbar */}
      <div className="flex items-center gap-2 p-2 border-b bg-background">
        <Button
          variant="ghost"
          size="sm"
          asChild
          className="h-8 px-2 text-muted-foreground hover:text-foreground"
        >
          <Link href="/meetings">
            <ArrowLeft className="h-4 w-4" />
          </Link>
        </Button>
        <Monitor className="h-4 w-4 text-muted-foreground" />
        <span className="text-sm font-medium">Session #{meeting.id}</span>
        <div className="flex-1" />
        <Button
          variant="outline"
          size="sm"
          onClick={handleSave}
          disabled={isSaving}
        >
          {isSaving ? (
            <Loader2 className="h-4 w-4 animate-spin mr-1" />
          ) : (
            <Save className="h-4 w-4 mr-1" />
          )}
          Save
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={handleDeleteStorage}
          disabled={isDeleting}
          className="text-destructive hover:text-destructive"
        >
          {isDeleting ? (
            <Loader2 className="h-4 w-4 animate-spin mr-1" />
          ) : (
            <Trash2 className="h-4 w-4 mr-1" />
          )}
          Clear Storage
        </Button>
        <Button
          variant={showPanel ? "default" : "outline"}
          size="sm"
          onClick={() => setShowPanel(!showPanel)}
        >
          {showPanel ? (
            <PanelRightClose className="h-4 w-4 mr-1" />
          ) : (
            <PanelRightOpen className="h-4 w-4 mr-1" />
          )}
          Connect Agent
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            if (vncUrl) window.open(vncUrl, "_blank");
          }}
        >
          <ExternalLink className="h-4 w-4 mr-1" />
          Fullscreen
        </Button>
        <Button
          variant="destructive"
          size="sm"
          onClick={handleStop}
          disabled={isStopping}
        >
          {isStopping ? (
            <Loader2 className="h-4 w-4 animate-spin mr-1" />
          ) : (
            <Trash2 className="h-4 w-4 mr-1" />
          )}
          Stop
        </Button>
      </div>

      {/* Content area */}
      <div className="flex flex-1 overflow-hidden">
        {/* VNC iframe */}
        <div className="flex-1 min-w-0">
          {vncUrl ? (
            <iframe
              src={vncUrl}
              className="w-full h-full border-0"
              allow="clipboard-read; clipboard-write"
            />
          ) : (
            <div className="h-full flex items-center justify-center">
              <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
            </div>
          )}
        </div>

        {/* Connect Agent sidebar */}
        {showPanel && (
          <div className="w-96 border-l bg-card p-4 flex flex-col gap-4 overflow-y-auto">
            <div>
              <h3 className="font-semibold text-sm mb-1">Connect Agent</h3>
              <p className="text-xs text-muted-foreground">
                Copy the instructions below into Claude or any AI agent to give
                it control of this browser.
              </p>
            </div>

            <CopyBlock label="Agent Instructions" text={agentInstructions} />

            <hr />

            <CopyBlock label="CDP URL" text={cdpUrl || ""} />

            {sshPort && (
              <>
                <hr />
                <CopyBlock
                  label="SSH"
                  text={`ssh root@${sshHost} -p ${sshPort}\nPassword: ${token}`}
                />
              </>
            )}

            <hr />

            <div className="space-y-2">
              <h4 className="text-xs font-medium">MCP Server</h4>
              <p className="text-xs text-muted-foreground">
                Connect Claude Desktop or any MCP client.
              </p>
              <CopyBlock label="MCP Endpoint" text={mcpUrl || ""} />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
