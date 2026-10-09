"use client";

import { useEffect, useRef } from "react";
import { MessageSquare } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useMeetingsStore } from "@/stores/meetings-store";
import type { ChatMessage, Platform } from "@/types/vexa";
import { cn } from "@/lib/utils";

interface ChatPanelProps {
  platform: Platform;
  nativeId: string;
  isActive: boolean;
}

function formatTimestamp(ts: number): string {
  const date = new Date(ts);
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function ChatBubble({ message }: { message: ChatMessage }) {
  return (
    <div className={cn("flex flex-col gap-0.5", message.is_from_bot ? "items-end" : "items-start")}>
      <span className="text-[10px] text-muted-foreground px-1">
        {message.sender} &middot; {formatTimestamp(message.timestamp)}
      </span>
      <div
        className={cn(
          "rounded-lg px-3 py-1.5 text-sm max-w-[85%] break-words",
          message.is_from_bot
            ? "bg-primary text-primary-foreground"
            : "bg-muted"
        )}
      >
        {message.text}
      </div>
    </div>
  );
}

export function ChatPanel({ platform, nativeId, isActive }: ChatPanelProps) {
  const chatMessages = useMeetingsStore((state) => state.chatMessages);
  const fetchChatMessages = useMeetingsStore((state) => state.fetchChatMessages);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Bootstrap chat messages from REST on mount
  useEffect(() => {
    if (platform && nativeId) {
      fetchChatMessages(platform, nativeId);
    }
  }, [platform, nativeId, fetchChatMessages]);

  // Auto-scroll to bottom when new messages arrive
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [chatMessages.length]);

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2">
          <MessageSquare className="h-4 w-4" />
          Chat{chatMessages.length > 0 ? ` (${chatMessages.length})` : ""}
        </CardTitle>
      </CardHeader>
      <CardContent>
        {chatMessages.length === 0 ? (
          <p className="text-sm text-muted-foreground italic text-center py-4">
            No chat messages yet
          </p>
        ) : (
          <div
            ref={scrollRef}
            className="space-y-2 max-h-[300px] overflow-y-auto pr-1"
          >
            {chatMessages.map((msg, i) => (
              <ChatBubble key={`${msg.timestamp}-${i}`} message={msg} />
            ))}
          </div>
        )}
        {isActive && (
          <p className="text-[10px] text-muted-foreground mt-2 text-center">
            Live &mdash; messages appear in real time
          </p>
        )}
      </CardContent>
    </Card>
  );
}
