"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import {
  Send, Square, RotateCcw, Loader2, Wrench, Bot, User,
  Plus, MessageSquare, Trash2, Pencil, Check, X,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useAgentStore, AgentMessage } from "@/stores/agent-store";
import { useAuthStore } from "@/stores/auth-store";

const AGENT_API = process.env.NEXT_PUBLIC_AGENT_API_URL || "/api/agent";

function ToolChip({ tool, summary }: { tool: string; summary: string }) {
  return (
    <Badge variant="secondary" className="text-xs gap-1 font-normal">
      <Wrench className="h-3 w-3" />
      {summary || tool}
    </Badge>
  );
}

function MessageBubble({ msg }: { msg: AgentMessage }) {
  const isUser = msg.role === "user";
  return (
    <div className={`flex gap-3 ${isUser ? "justify-end" : "justify-start"}`}>
      {!isUser && (
        <div className="flex-shrink-0 w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center">
          <Bot className="h-4 w-4 text-primary" />
        </div>
      )}
      <div className={`max-w-[80%] ${isUser ? "order-first" : ""}`}>
        <div
          className={`rounded-lg px-4 py-2 ${
            isUser
              ? "bg-primary text-primary-foreground"
              : "bg-muted"
          }`}
        >
          {isUser ? (
            <p className="text-sm whitespace-pre-wrap">{msg.content}</p>
          ) : (
            <div className="text-sm prose prose-sm dark:prose-invert max-w-none">
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
        <div className="flex-shrink-0 w-8 h-8 rounded-full bg-primary flex items-center justify-center">
          <User className="h-4 w-4 text-primary-foreground" />
        </div>
      )}
    </div>
  );
}

function SessionSidebar() {
  const {
    sessions, activeSessionId, sessionsLoaded,
    createSession, deleteSession, renameSession, setActiveSession, loadSessions,
  } = useAgentStore();
  const { user } = useAuthStore();
  const userId = user?.id?.toString() || user?.email || "default";

  const [newName, setNewName] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editName, setEditName] = useState("");

  useEffect(() => {
    if (!sessionsLoaded) loadSessions(userId);
  }, [userId, sessionsLoaded, loadSessions]);

  const handleCreate = async () => {
    const name = newName.trim() || `Session ${new Date().toLocaleDateString()}`;
    await createSession(name);
    setNewName("");
  };

  return (
    <div className="w-56 border-r flex flex-col bg-muted/30">
      <div className="p-2 border-b">
        <div className="flex gap-1">
          <Input
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="New session..."
            className="h-8 text-xs"
            onKeyDown={(e) => e.key === "Enter" && handleCreate()}
          />
          <Button size="sm" variant="ghost" className="h-8 w-8 p-0" onClick={handleCreate}>
            <Plus className="h-4 w-4" />
          </Button>
        </div>
      </div>
      <div className="flex-1 overflow-y-auto">
        {sessions.length === 0 && (
          <p className="text-xs text-muted-foreground p-3">
            No sessions yet. Create one to start.
          </p>
        )}
        {sessions.map((session) => (
          <div
            key={session.id}
            className={`group flex items-center gap-1 px-2 py-1.5 cursor-pointer text-sm hover:bg-muted ${
              session.id === activeSessionId ? "bg-muted font-medium" : ""
            }`}
            onClick={() => setActiveSession(session.id)}
          >
            <MessageSquare className="h-3.5 w-3.5 flex-shrink-0 text-muted-foreground" />
            {editingId === session.id ? (
              <div className="flex items-center gap-1 flex-1 min-w-0">
                <Input
                  value={editName}
                  onChange={(e) => setEditName(e.target.value)}
                  className="h-6 text-xs flex-1"
                  autoFocus
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      renameSession(session.id, editName);
                      setEditingId(null);
                    }
                    if (e.key === "Escape") setEditingId(null);
                  }}
                  onClick={(e) => e.stopPropagation()}
                />
                <Check
                  className="h-3 w-3 cursor-pointer text-green-500"
                  onClick={(e) => {
                    e.stopPropagation();
                    renameSession(session.id, editName);
                    setEditingId(null);
                  }}
                />
              </div>
            ) : (
              <>
                <span className="flex-1 truncate text-xs">{session.name}</span>
                <div className="hidden group-hover:flex items-center gap-0.5">
                  <Pencil
                    className="h-3 w-3 text-muted-foreground hover:text-foreground"
                    onClick={(e) => {
                      e.stopPropagation();
                      setEditingId(session.id);
                      setEditName(session.name);
                    }}
                  />
                  <Trash2
                    className="h-3 w-3 text-muted-foreground hover:text-destructive"
                    onClick={(e) => {
                      e.stopPropagation();
                      deleteSession(session.id);
                    }}
                  />
                </div>
              </>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

export function AgentChat() {
  const {
    messages, isStreaming, activeSessionId,
    addMessage, updateLastAssistant, setStreaming, clearMessages,
  } = useAgentStore();

  const { user } = useAuthStore();
  const userId = user?.id?.toString() || user?.email || "default";

  const [input, setInput] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  const sendMessage = useCallback(async () => {
    const msg = input.trim();
    if (!msg || isStreaming) return;
    setInput("");

    addMessage({
      id: `user-${Date.now()}`,
      role: "user",
      content: msg,
      timestamp: Date.now(),
    });

    addMessage({
      id: `assistant-${Date.now()}`,
      role: "assistant",
      content: "",
      tools: [],
      timestamp: Date.now(),
    });

    setStreaming(true);
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const body: Record<string, any> = { user_id: userId, message: msg };
      if (activeSessionId) {
        body.session_id = activeSessionId;
      }

      const resp = await fetch(`${AGENT_API}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal: controller.signal,
      });

      if (!resp.ok || !resp.body) {
        updateLastAssistant(`Error: ${resp.status} ${resp.statusText}`);
        setStreaming(false);
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
              const currentMessages = useAgentStore.getState().messages;
              const lastTwo = currentMessages.slice(-2);
              useAgentStore.setState({ messages: lastTwo });
              accumulated = "*Session restarted — previous context is no longer available.*\n\n";
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
              // Update session ID if returned
              if (event.session_id && !activeSessionId) {
                // Auto-create session entry for new sessions
                useAgentStore.getState().createSession("New session");
              }
            } else if (event.type === "error") {
              accumulated += `\n\n⚠️ ${event.message}`;
              updateLastAssistant(accumulated, tools);
            }
          } catch {}
        }
      }
    } catch (err: any) {
      if (err.name !== "AbortError") {
        updateLastAssistant(`Error: ${err.message}`);
      }
    } finally {
      setStreaming(false);
      abortRef.current = null;
    }
  }, [input, isStreaming, userId, activeSessionId, addMessage, updateLastAssistant, setStreaming]);

  const handleStop = useCallback(async () => {
    abortRef.current?.abort();
    try {
      await fetch(`${AGENT_API}/chat`, {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: userId }),
      });
    } catch {}
    setStreaming(false);
  }, [userId, setStreaming]);

  const handleReset = useCallback(async () => {
    await handleStop();
    try {
      await fetch(`${AGENT_API}/chat/reset`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: userId }),
      });
    } catch {}
    clearMessages();
  }, [userId, handleStop, clearMessages]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  return (
    <div className="flex h-full">
      <SessionSidebar />
      <div className="flex flex-col flex-1 min-w-0">
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b">
          <div className="flex items-center gap-2">
            <Bot className="h-5 w-5 text-primary" />
            <h2 className="font-semibold text-sm">
              {useAgentStore.getState().sessions.find(s => s.id === activeSessionId)?.name || "Vexa Agent"}
            </h2>
            {isStreaming && (
              <Badge variant="secondary" className="text-xs">
                <Loader2 className="h-3 w-3 animate-spin mr-1" />
                Thinking...
              </Badge>
            )}
          </div>
          <Button variant="ghost" size="sm" onClick={handleReset}>
            <RotateCcw className="h-4 w-4 mr-1" />
            Reset
          </Button>
        </div>

        {/* Messages */}
        <div ref={scrollRef} className="flex-1 overflow-y-auto p-4 space-y-4">
          {!activeSessionId && messages.length === 0 && (
            <div className="flex flex-col items-center justify-center h-full text-muted-foreground gap-2">
              <Bot className="h-10 w-10" />
              <p>Create a session to start chatting.</p>
              <p className="text-xs">Or just type — a default session will be created.</p>
            </div>
          )}
          {messages.map((msg) => (
            <MessageBubble key={msg.id} msg={msg} />
          ))}
        </div>

        {/* Input */}
        <div className="border-t p-4">
          <div className="flex gap-2">
            <Input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Message your agent..."
              disabled={isStreaming}
              className="flex-1"
            />
            {isStreaming ? (
              <Button variant="destructive" onClick={handleStop}>
                <Square className="h-4 w-4" />
              </Button>
            ) : (
              <Button onClick={sendMessage} disabled={!input.trim()}>
                <Send className="h-4 w-4" />
              </Button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
