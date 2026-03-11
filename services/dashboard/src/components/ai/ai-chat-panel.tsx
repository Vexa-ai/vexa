"use client";

import { useEffect, useRef, useCallback, useState, useMemo } from "react";
import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport } from "ai";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  MessageSquare,
  Send,
  Loader2,
  Trash2,
  StopCircle,
  XCircle,
  X,
  Bot,
  User,
  AlertCircle,
} from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";
import type { Meeting, TranscriptSegment } from "@/types/vexa";

interface AIChatPanelProps {
  meeting?: Meeting;
  transcripts?: TranscriptSegment[];
  trigger?: React.ReactNode;
}

interface AIConfig {
  enabled: boolean;
  provider: string | null;
  model: string | null;
}

function buildTranscriptContext(transcripts: TranscriptSegment[], meeting?: Meeting): string {
  if (!transcripts.length) {
    return "";
  }

  let context = "";

  if (meeting) {
    context += `Meeting: ${meeting.data?.name || meeting.data?.title || meeting.platform_specific_id}\n`;
    context += `Platform: ${meeting.platform}\n`;
    if (meeting.data?.participants?.length) {
      context += `Participants: ${meeting.data.participants.join(", ")}\n`;
    }
    context += "\n---\n\n";
  }

  context += "TRANSCRIPT:\n\n";

  // Group by speaker for cleaner context
  let lastSpeaker = "";
  for (const segment of transcripts) {
    if (segment.speaker !== lastSpeaker) {
      context += `\n[${segment.speaker}]:\n`;
      lastSpeaker = segment.speaker;
    }
    context += `${segment.text} `;
  }

  return context.trim();
}

export function AIChatPanel({ meeting, transcripts = [], trigger }: AIChatPanelProps) {
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState("");
  const [aiConfig, setAIConfig] = useState<AIConfig | null>(null);
  const [isLoadingConfig, setIsLoadingConfig] = useState(true);
  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Check if AI is configured on mount
  useEffect(() => {
    async function checkAIConfig() {
      try {
        const response = await fetch("/api/ai/config");
        const config = await response.json();
        setAIConfig(config);
      } catch (error) {
        console.error("Failed to check AI config:", error);
        setAIConfig({ enabled: false, provider: null, model: null });
      } finally {
        setIsLoadingConfig(false);
      }
    }
    checkAIConfig();
  }, []);

  // Build context from transcripts
  const context = buildTranscriptContext(transcripts, meeting);

  // Memoize the transport - no settings needed anymore
  const transport = useMemo(() => {
    return new DefaultChatTransport({
      api: "/api/ai/chat",
      body: { context },
    });
  }, [context]);

  const {
    messages,
    status,
    error,
    setMessages,
    sendMessage,
    stop,
    clearError,
  } = useChat({
    transport,
  });

  const isLoading = status === "streaming" || status === "submitted";
  const isConfigured = aiConfig?.enabled ?? false;

  // Auto-scroll to bottom
  useEffect(() => {
    if (scrollRef.current) {
      const scrollElement = scrollRef.current.querySelector('[data-radix-scroll-area-viewport]');
      if (scrollElement) {
        scrollElement.scrollTop = scrollElement.scrollHeight;
      }
    }
  }, [messages, status]);

  // Focus textarea when opened
  useEffect(() => {
    if (open && textareaRef.current) {
      setTimeout(() => textareaRef.current?.focus(), 100);
    }
  }, [open]);

  const handleClear = useCallback(() => {
    setMessages([]);
    clearError();
  }, [setMessages, clearError]);

  const doSendMessage = useCallback((text: string) => {
    if (text.trim() && !isLoading) {
      clearError();
      sendMessage({
        parts: [{ type: "text" as const, text: text.trim() }],
      });
    }
  }, [isLoading, sendMessage, clearError]);

  const handleSubmit = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault();
      doSendMessage(input);
      setInput("");
    },
    [input, doSendMessage]
  );

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        doSendMessage(input);
        setInput("");
      }
    },
    [input, doSendMessage]
  );

  // Send suggested prompt immediately
  const handleSuggestedPrompt = useCallback((prompt: string) => {
    doSendMessage(prompt);
  }, [doSendMessage]);

  const suggestedPrompts = [
    "Summarize this meeting in key points",
    "What were the main decisions made?",
    "List all action items and who is responsible",
    "What topics were discussed?",
    "Are there any unresolved questions?",
    "What are the next steps?",
  ];

  // Get text content from message parts
  const getMessageContent = (message: typeof messages[0]): string => {
    const parts = message.parts;
    if (Array.isArray(parts)) {
      return parts
        .filter((part): part is { type: "text"; text: string } => part.type === "text")
        .map(part => part.text)
        .join("");
    }
    return "";
  };

  // Get user-friendly error message
  const getErrorMessage = (err: Error): string => {
    const msg = err.message.toLowerCase();
    if (msg.includes("not configured") || msg.includes("503")) {
      return "AI is not configured on this server. Contact your administrator.";
    }
    if (msg.includes("invalid api key") || msg.includes("incorrect api key") || msg.includes("401")) {
      return "Invalid API key configured. Contact your administrator.";
    }
    if (msg.includes("rate limit") || msg.includes("429")) {
      return "Rate limit exceeded. Please wait a moment and try again.";
    }
    if (msg.includes("insufficient") || msg.includes("quota")) {
      return "API quota exceeded. Contact your administrator.";
    }
    if (msg.includes("network") || msg.includes("fetch")) {
      return "Network error. Please check your connection.";
    }
    return err.message;
  };

  // Don't render trigger if AI is not configured
  if (!isLoadingConfig && !isConfigured) {
    return null;
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        {trigger || (
          <Button variant="outline" size="sm" className="gap-2">
            <MessageSquare className="h-4 w-4" />
            Ask AI
          </Button>
        )}
      </DialogTrigger>
      <DialogContent className="max-w-5xl w-[95vw] h-[90vh] flex flex-col p-0 gap-0">
        {/* Header */}
        <DialogHeader className="px-6 py-4 border-b shrink-0">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="h-9 w-9 rounded-lg bg-muted flex items-center justify-center">
                <Bot className="h-5 w-5 text-foreground" />
              </div>
              <div>
                <DialogTitle className="text-base font-medium">AI Assistant</DialogTitle>
                {meeting && (
                  <p className="text-sm text-muted-foreground">
                    {meeting.data?.name || meeting.data?.title || meeting.platform_specific_id}
                  </p>
                )}
              </div>
            </div>
            <div className="flex items-center gap-2">
              {messages.length > 0 && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={handleClear}
                  className="gap-2 text-muted-foreground hover:text-foreground"
                >
                  <Trash2 className="h-4 w-4" />
                  Clear
                </Button>
              )}
            </div>
          </div>
        </DialogHeader>

        {/* Error Banner */}
        {error && (
          <div className="mx-6 mt-4 p-4 rounded-lg bg-destructive/10 border border-destructive/20">
            <div className="flex items-start gap-3">
              <XCircle className="h-5 w-5 text-destructive shrink-0 mt-0.5" />
              <div className="flex-1 min-w-0">
                <p className="font-medium text-destructive">Something went wrong</p>
                <p className="text-sm text-destructive/80 mt-1">{getErrorMessage(error)}</p>
              </div>
              <Button
                variant="ghost"
                size="icon"
                onClick={clearError}
                className="h-8 w-8 text-destructive hover:text-destructive shrink-0"
              >
                <X className="h-4 w-4" />
              </Button>
            </div>
          </div>
        )}

        {/* Messages Area */}
        <ScrollArea className="flex-1 min-h-0" ref={scrollRef}>
          <div className="p-6">
            {isLoadingConfig ? (
              <div className="h-full flex flex-col items-center justify-center text-center py-16">
                <Loader2 className="h-8 w-8 animate-spin text-muted-foreground mb-4" />
                <p className="text-muted-foreground">Checking AI availability...</p>
              </div>
            ) : !isConfigured ? (
              <div className="h-full flex flex-col items-center justify-center text-center py-16">
                <div className="h-20 w-20 rounded-2xl bg-muted flex items-center justify-center mb-6">
                  <AlertCircle className="h-10 w-10 text-muted-foreground" />
                </div>
                <h3 className="text-xl font-semibold mb-2">AI Not Configured</h3>
                <p className="text-muted-foreground mb-6 max-w-md">
                  The AI assistant is not configured on this server. Contact your administrator to enable it.
                </p>
              </div>
            ) : messages.length === 0 && !error ? (
              <div className="flex flex-col items-center justify-center text-center py-12">
                <div className="h-16 w-16 rounded-full bg-muted flex items-center justify-center mb-6">
                  <MessageSquare className="h-8 w-8 text-muted-foreground" />
                </div>
                <h3 className="text-lg font-medium mb-2">Ask anything about your meeting</h3>
                <p className="text-sm text-muted-foreground mb-8 max-w-md">
                  {transcripts.length > 0
                    ? `${transcripts.length} transcript segments loaded`
                    : "No transcript loaded yet"}
                </p>
                {transcripts.length > 0 && (
                  <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2 w-full max-w-3xl">
                    {suggestedPrompts.map((prompt) => (
                      <button
                        key={prompt}
                        onClick={() => handleSuggestedPrompt(prompt)}
                        disabled={isLoading}
                        className="text-left text-sm px-4 py-3 rounded-lg border bg-background hover:bg-muted transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                      >
                        {prompt}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            ) : (
              <div className="space-y-6 max-w-4xl mx-auto">
                {messages.map((message) => (
                  <div key={message.id} className="flex gap-3">
                    {/* Avatar */}
                    <div className={cn(
                      "h-8 w-8 rounded-full flex items-center justify-center shrink-0",
                      message.role === "user"
                        ? "bg-foreground text-background"
                        : "bg-muted text-foreground"
                    )}>
                      {message.role === "user" ? (
                        <User className="h-4 w-4" />
                      ) : (
                        <Bot className="h-4 w-4" />
                      )}
                    </div>

                    {/* Message Content */}
                    <div className="flex-1 min-w-0 pt-0.5">
                      <p className="text-xs font-medium mb-1.5 text-muted-foreground">
                        {message.role === "user" ? "You" : "Assistant"}
                      </p>
                      {message.role === "user" ? (
                        <p className="text-sm text-foreground whitespace-pre-wrap">
                          {getMessageContent(message)}
                        </p>
                      ) : (
                        <div className="prose prose-sm dark:prose-invert max-w-none prose-p:leading-relaxed prose-pre:bg-muted prose-pre:border prose-headings:font-semibold prose-ul:my-2 prose-li:my-0">
                          <ReactMarkdown remarkPlugins={[remarkGfm]}>
                            {getMessageContent(message)}
                          </ReactMarkdown>
                        </div>
                      )}
                    </div>
                  </div>
                ))}

                {/* Loading indicator */}
                {isLoading && (
                  <div className="flex gap-3">
                    <div className="h-8 w-8 rounded-full bg-muted flex items-center justify-center shrink-0">
                      <Bot className="h-4 w-4 text-foreground" />
                    </div>
                    <div className="flex-1 pt-0.5">
                      <p className="text-xs font-medium mb-1.5 text-muted-foreground">Assistant</p>
                      <div className="flex items-center gap-2 text-muted-foreground">
                        <Loader2 className="h-3 w-3 animate-spin" />
                        <span className="text-sm">Thinking...</span>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </ScrollArea>

        {/* Input Area */}
        {isConfigured && (
          <div className="px-6 py-4 border-t shrink-0">
            <form onSubmit={handleSubmit} className="max-w-4xl mx-auto">
              <div className="flex gap-2">
                <Textarea
                  ref={textareaRef}
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder="Ask a question about your meeting..."
                  className="min-h-[44px] max-h-32 resize-none"
                  rows={1}
                  disabled={isLoading}
                />
                {isLoading ? (
                  <Button
                    type="button"
                    size="icon"
                    variant="outline"
                    onClick={stop}
                    className="shrink-0 h-11 w-11"
                  >
                    <StopCircle className="h-4 w-4" />
                  </Button>
                ) : (
                  <Button
                    type="submit"
                    size="icon"
                    disabled={!input.trim()}
                    className="shrink-0 h-11 w-11"
                  >
                    <Send className="h-4 w-4" />
                  </Button>
                )}
              </div>
              <p className="text-xs text-muted-foreground mt-2 text-center">
                Press Enter to send, Shift+Enter for new line
              </p>
            </form>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
