"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Code2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { getDocsUrl } from "@/lib/docs/webapp-url";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

interface DocsLinkProps {
  href: string;
  className?: string;
  label?: string;
}

// Extract meaningful API method name from href
function getMethodName(href: string): string {
  // Extract anchor/hash part
  const hash = href.split("#")[1] || "";
  
  // Map common patterns to readable names
  const methodMap: Record<string, string> = {
    "create-bot": "POST /bots",
    "stop-bot": "DELETE /bots/{platform}/{native_meeting_id}",
    "update-bot-configuration": "PATCH /bots/{platform}/{native_meeting_id}",
    "get-bot-status": "GET /bots/status",
    "list-meetings": "GET /meetings",
    "get-meeting": "GET /meetings/{platform}/{native_meeting_id}",
    "update-meeting-data": "PATCH /meetings/{platform}/{native_meeting_id}",
    "get-transcripts": "GET /transcripts/{platform}/{native_meeting_id}",
    "create-transcript-share-link": "POST /transcripts/{platform}/{native_meeting_id}/share",
    "create-user": "POST /admin/users",
    "create-api-token": "POST /admin/users/{user_id}/tokens",
    "revoke-token": "DELETE /admin/tokens/{token_id}",
  };

  // Check cookbook pages
  const cookbookMap: Record<string, string> = {
    "start-transcription": "POST /bots",
    "track-meeting-status": "WebSocket: meeting.status",
    "get-transcripts": "GET /transcripts",
    "live-transcripts": "WebSocket: transcript.segment",
    "share-transcript-url": "POST /transcripts/{platform}/{native_meeting_id}/share",
    "rename-meeting": "PATCH /meetings/{platform}/{native_meeting_id}",
    "get-status-history": "GET /meetings/{platform}/{native_meeting_id}",
    "stop-bot": "DELETE /bots/{platform}/{native_meeting_id}",
  };

  // Check if it's a cookbook page
  if (href.includes("/cookbook/")) {
    const cookbookName = href.split("/cookbook/")[1]?.split("#")[0];
    if (cookbookName && cookbookMap[cookbookName]) {
      return cookbookMap[cookbookName];
    }
  }

  // Check direct hash mapping
  if (hash && methodMap[hash]) {
    return methodMap[hash];
  }

  // Fallback: parse from path
  if (href.includes("/rest/bots")) {
    if (hash === "create-bot") return "POST /bots";
    if (hash === "stop-bot") return "DELETE /bots/{platform}/{native_meeting_id}";
    if (hash === "update-bot-configuration") return "PATCH /bots/{platform}/{native_meeting_id}";
    return "Bots API";
  }
  if (href.includes("/rest/meetings")) {
    if (hash === "update-meeting-data") return "PATCH /meetings/{platform}/{native_meeting_id}";
    if (hash === "list-meetings") return "GET /meetings";
    if (hash === "get-meeting") return "GET /meetings/{platform}/{native_meeting_id}";
    return "Meetings API";
  }
  if (href.includes("/rest/transcripts")) {
    if (hash === "get-transcripts") return "GET /transcripts/{platform}/{native_meeting_id}";
    if (hash === "create-transcript-share-link") return "POST /transcripts/{platform}/{native_meeting_id}/share";
    return "Transcripts API";
  }
  if (href.includes("/admin/users")) {
    if (hash === "create-user") return "POST /admin/users";
    if (hash === "create-api-token") return "POST /admin/users/{user_id}/tokens";
    return "Admin Users API";
  }
  if (href.includes("/admin/tokens")) {
    if (hash === "revoke-token") return "DELETE /admin/tokens/{token_id}";
    return "Admin Tokens API";
  }

  return "API Documentation";
}

export function DocsLink({ href, className, label = "api" }: DocsLinkProps) {
  const [mounted, setMounted] = useState(false);
  const [enabled, setEnabled] = useState(false);

  useEffect(() => {
    setMounted(true);
    
    // Function to read from localStorage
    const readDocsMode = () => {
      try {
        const stored = localStorage.getItem("vexa-docs-mode");
        if (stored) {
          const parsed = JSON.parse(stored);
          setEnabled(parsed?.state?.enabled ?? false);
        }
      } catch {
        // Ignore localStorage errors
      }
    };

    // Read initial value
    readDocsMode();

    // Listen for storage changes to sync across tabs
    const handleStorageChange = () => {
      readDocsMode();
    };

    // Listen for custom event for same-tab updates
    const handleCustomStorageChange = () => {
      readDocsMode();
    };

    window.addEventListener("storage", handleStorageChange);
    window.addEventListener("vexa-docs-mode-change", handleCustomStorageChange);
    
    return () => {
      window.removeEventListener("storage", handleStorageChange);
      window.removeEventListener("vexa-docs-mode-change", handleCustomStorageChange);
    };
  }, []);

  if (!mounted || !enabled) return null;

  const methodName = getMethodName(href);
  const docsUrl = getDocsUrl(href);

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <a
          href={docsUrl}
          target="_blank"
          rel="noopener noreferrer"
          className={cn(
            "inline-flex items-center justify-center gap-1.5 text-xs font-medium transition-all",
            "ml-2 px-2 py-1 rounded-md",
            "bg-muted text-muted-foreground",
            "border border-border",
            "hover:bg-muted/80",
            "hover:shadow-sm",
            className
          )}
          onClick={(e) => {
            e.stopPropagation();
          }}
        >
          <Code2 className="h-3.5 w-3.5 shrink-0" />
        </a>
      </TooltipTrigger>
      <TooltipContent>
        <p className="font-mono text-xs">{methodName}</p>
      </TooltipContent>
    </Tooltip>
  );
}

