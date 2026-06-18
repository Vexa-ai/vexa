"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import {
  Send, Square, RotateCcw, Loader2, Wrench, Bot, User,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useAuthStore } from "@/stores/auth-store";

const AGENT_API = process.env.NEXT_PUBLIC_AGENT_API_URL || "/api/agent";

interface LocalMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  tools?: { tool: string; summary: string }[];
  timestamp: number;
}

function ToolChip({ tool, summary }: { tool: string; summary: string }) {
  return (
    <Badge variant="secondary" className="text-xs gap-1 font-normal">
      <Wrench className="h-3 w-3" />
      {summary || tool}
    </Badge>
  );
}

function MessageBubble({ msg }: { msg: LocalMessage }) {
  const isUser = msg.role === "user";
  return (
    <div className={`flex gap-2 ${isUser ? "justify-end" : "justify-start"}`}>
      {!isUser && (
        <div className="flex-shrink-0 w-7 h-7 rounded-full bg-primary/10 flex items-center justify-center">
          <Bot className="h-3.5 w-3.5 text-primary" />
        </div>
      )}
      <div className={`max-w-[85%] ${isUser ? "order-first" : ""}`}>
        <div
          className={`rounded-lg px-3 py-2 ${
            isUser
              ? "bg-primary text-primary-foreground"
              : "bg-muted"
          }`}
        >
          {isUser ? (
            <p className="text-xs whitespace-pre-wrap">{msg.content}</p>
          ) : (
            <div className="text-xs prose prose-xs dark:prose-invert max-w-none">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {msg.content || "..."}
              </ReactMarkdown>
            </div>
          )}
        </div>
        {msg.tools && msg.tools.length > 0 && (
          <div className="flex flex-wrap gap-1 mt-1">
            {msg.tools.map((t, i) => (
              <ToolChip key={i} tool={t.tool} summary={t.summary} />
            ))}
          </div>
        )}
      </div>
      {isUser && (
        <div className="flex-shrink-0 w-7 h-7 rounded-full bg-primary flex items-center justify-center">
          <User className="h-3.5 w-3.5 text-primary-foreground" />
        </div>
      )}
    </div>
  );
}

interface MeetingAgentPanelProps {
  meetingId: string;
  platform: string;
}

export function MeetingAgentPanel({ meetingId, platform }: MeetingAgentPanelProps) {
  const { user } = useAuthStore();
  const userId = user?.id?.toString() || user?.email || "default";

  // Local state — independent from the global agent store
  const [messages, setMessages] = useState<LocalMessage[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [input, setInput] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  // Each meeting panel gets its own session to isolate context
  const sessionIdRef = useRef<string | null>(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  const addMessage = useCallback((msg: LocalMessage) => {
    setMessages((prev) => [...prev, msg]);
  }, []);

  const updateLastAssistant = useCallback(
    (content: string, tools?: { tool: string; summary: string }[]) => {
      setMessages((prev) => {
        const msgs = [...prev];
        const last = msgs[msgs.length - 1];
        if (last && last.role === "assistant") {
          msgs[msgs.length - 1] = { ...last, content, tools: tools || last.tools };
        }
        return msgs;
      });
    },
    []
  );

  const sendMessage = useCallback(async () => {
    const rawMsg = input.trim();
    if (!rawMsg || isStreaming) return;
    setInput("");

    // Prepend meeting context so the agent knows which meeting to help with
    const contextPrefix = `[Meeting context: ${platform} meeting ${meetingId}. Use \`vexa meeting transcript ${meetingId}\` to read the transcript.]\n\n`;
    const fullMsg = contextPrefix + rawMsg;

    addMessage({
      id: `user-${Date.now()}`,
      role: "user",
      // Show the user only their actual message, not the injected context
      content: rawMsg,
      timestamp: Date.now(),
    });

    addMessage({
      id: `assistant-${Date.now()}`,
      role: "assistant",
      content: "",
      tools: [],
      timestamp: Date.now(),
    });

    setIsStreaming(true);
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const body: Record<string, unknown> = {
        user_id: userId,
        message: fullMsg,
      };
      if (sessionIdRef.current) {
        body.session_id = sessionIdRef.current;
      }

      const resp = await fetch(`${AGENT_API}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal: controller.signal,
      });

      if (!resp.ok || !resp.body) {
        updateLastAssistant(`Error: ${resp.status} ${resp.statusText}`);
        setIsStreaming(false);
        return;
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let accumulated = "";
      let tools: { tool: string; summary: string }[] = [];

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        const chunk = decoder.decode(value, { stream: true });
        for (const line of chunk.split("\n")) {
          if (!line.startsWith("data: ")) continue;
          try {
            const event = JSON.parse(line.slice(6));
            if (event.type === "session_reset") {
              // Keep only the last exchange so context resets gracefully
              setMessages((prev) => prev.slice(-2));
              accumulated = "*Session restarted — previous context cleared.*\n\n";
              updateLastAssistant(accumulated, tools);
            } else if (event.type === "reconnecting") {
              if (accumulated) {
                updateLastAssistant(accumulated + "\n\n---\n*Reconnecting...*", tools);
              }
              accumulated = "";
              tools = [];
              addMessage({
                id: `assistant-${Date.now()}`,
                role: "assistant",
                content: "",
                tools: [],
                timestamp: Date.now(),
              });
            } else if (event.type === "text_delta") {
              if (tools.length > 0 && accumulated.length > 0 && !accumulated.endsWith("\n")) {
                accumulated += "\n\n";
              }
              accumulated += event.text || "";
              updateLastAssistant(accumulated, tools);
            } else if (event.type === "tool_use") {
              tools = [...tools, { tool: event.tool, summary: event.summary }];
              updateLastAssistant(accumulated, tools);
            } else if (event.type === "stream_end") {
              // Capture the session ID for subsequent messages in this panel
              if (event.session_id && !sessionIdRef.current) {
                sessionIdRef.current = event.session_id;
              }
            } else if (event.type === "error") {
              accumulated += `\n\n${event.message}`;
              updateLastAssistant(accumulated, tools);
            }
          } catch {
            // Ignore malformed SSE lines
          }
        }
      }
    } catch (err: unknown) {
      if (err instanceof Error && err.name !== "AbortError") {
        updateLastAssistant(`Error: ${err.message}`);
      }
    } finally {
      setIsStreaming(false);
      abortRef.current = null;
    }
  }, [input, isStreaming, userId, meetingId, platform, addMessage, updateLastAssistant]);

  const handleStop = useCallback(async () => {
    abortRef.current?.abort();
    try {
      await fetch(`${AGENT_API}/chat`, {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: userId }),
      });
    } catch {
      // Ignore stop errors
    }
    setIsStreaming(false);
  }, [userId]);

  const handleReset = useCallback(async () => {
    await handleStop();
    try {
      await fetch(`${AGENT_API}/chat/reset`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: userId }),
      });
    } catch {
      // Ignore reset errors
    }
    sessionIdRef.current = null;
    setMessages([]);
  }, [userId, handleStop]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  return (
    <div className="flex flex-col h-full min-h-0">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b shrink-0">
        <div className="flex items-center gap-2">
          <Bot className="h-4 w-4 text-primary" />
          <span className="font-semibold text-sm">Meeting Agent</span>
          {isStreaming && (
            <Badge variant="secondary" className="text-xs">
              <Loader2 className="h-3 w-3 animate-spin mr-1" />
              Thinking...
            </Badge>
          )}
        </div>
        <Button variant="ghost" size="sm" className="h-7 text-xs" onClick={handleReset}>
          <RotateCcw className="h-3 w-3 mr-1" />
          Reset
        </Button>
      </div>

      {/* Messages */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto p-3 space-y-3 min-h-0">
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full text-muted-foreground gap-2 py-8">
            <Bot className="h-8 w-8 opacity-50" />
            <p className="text-xs text-center">
              Ask me anything about this meeting.
              <br />
              I can read the transcript and answer questions.
            </p>
          </div>
        )}
        {messages.map((msg) => (
          <MessageBubble key={msg.id} msg={msg} />
        ))}
      </div>

      {/* Input */}
      <div className="border-t p-3 shrink-0">
        <div className="flex gap-2">
          <Input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask about this meeting..."
            disabled={isStreaming}
            className="flex-1 h-8 text-sm"
          />
          {isStreaming ? (
            <Button variant="destructive" size="sm" className="h-8 w-8 p-0" onClick={handleStop}>
              <Square className="h-3.5 w-3.5" />
            </Button>
          ) : (
            <Button
              size="sm"
              className="h-8 w-8 p-0"
              onClick={sendMessage}
              disabled={!input.trim()}
            >
              <Send className="h-3.5 w-3.5" />
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
