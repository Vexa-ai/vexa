"use client";

import { useState } from "react";
import { Sparkles, Loader2, Copy, Check, ChevronDown, ChevronRight } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/utils";

interface AiNotesData {
  summary?: string;
  key_moments?: Array<{ timestamp?: string; speaker?: string; text?: string }>;
  decisions?: string[];
  action_items?: Array<{ description?: string; assignee?: string; deadline?: string }>;
  unresolved?: string[];
  follow_up_email?: string;
}

interface AiNotesCardProps {
  meetingData?: Record<string, any>;
  meetingStatus?: string;
  onRegenerate?: () => void;
}

export function AiNotesCard({ meetingData, meetingStatus, onRegenerate }: AiNotesCardProps) {
  const aiNotes = meetingData?.ai_notes as AiNotesData | undefined;
  const generatedAt = meetingData?.ai_notes_generated_at;
  const model = meetingData?.ai_notes_model;
  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState(false);

  if (!aiNotes || !aiNotes.summary) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-sm">
            <Sparkles className="h-4 w-4 text-muted-foreground" />
            AI Notes
            {meetingStatus === "completed" && (
              <Badge variant="outline" className="ml-auto text-xs">
                Not generated
              </Badge>
            )}
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            AI notes are generated automatically after a meeting completes.
            {meetingStatus === "completed" && onRegenerate && (
              <Button variant="link" className="h-auto p-0 ml-2" onClick={onRegenerate}>
                Generate now
              </Button>
            )}
          </p>
        </CardContent>
      </Card>
    );
  }

  const handleCopy = () => {
    const text = formatNotesForCopy(aiNotes);
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="flex items-center gap-2 text-sm">
            <Sparkles className="h-4 w-4 text-primary" />
            AI Notes
            {model && <Badge variant="secondary" className="text-xs">{model}</Badge>}
          </CardTitle>
          <div className="flex items-center gap-1">
            <Button variant="ghost" size="sm" className="h-7 w-7 p-0" onClick={handleCopy}>
              {copied ? <Check className="h-3.5 w-3.5 text-green-500" /> : <Copy className="h-3.5 w-3.5" />}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="h-7 w-7 p-0"
              onClick={() => setExpanded(!expanded)}
            >
              {expanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-sm leading-relaxed">{aiNotes.summary}</p>

        {expanded && (
          <div className="space-y-3">
            <Separator />

            {(aiNotes.key_moments?.length || 0) > 0 && (
              <div>
                <h4 className="text-xs font-semibold text-muted-foreground mb-1.5">KEY MOMENTS</h4>
                <ul className="space-y-1">
                  {aiNotes.key_moments?.map((m, i) => (
                    <li key={i} className="text-xs">
                      {m.timestamp && <span className="text-muted-foreground">{m.timestamp}</span>}
                      {m.speaker && <span className="text-muted-foreground"> [{m.speaker}]</span>}
                      <span className="ml-1">{m.text}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {(aiNotes.decisions?.length || 0) > 0 && (
              <div>
                <h4 className="text-xs font-semibold text-muted-foreground mb-1.5">DECISIONS</h4>
                <ul className="space-y-1">
                  {aiNotes.decisions?.map((d, i) => (
                    <li key={i} className="text-xs">• {d}</li>
                  ))}
                </ul>
              </div>
            )}

            {(aiNotes.action_items?.length || 0) > 0 && (
              <div>
                <h4 className="text-xs font-semibold text-muted-foreground mb-1.5">ACTION ITEMS</h4>
                <ul className="space-y-1">
                  {aiNotes.action_items?.map((a, i) => (
                    <li key={i} className="text-xs">
                      □ {a.description}
                      {a.assignee && <span className="text-muted-foreground"> → {a.assignee}</span>}
                      {a.deadline && <span className="text-muted-foreground"> ({a.deadline})</span>}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {(aiNotes.unresolved?.length || 0) > 0 && (
              <div>
                <h4 className="text-xs font-semibold text-muted-foreground mb-1.5">OPEN QUESTIONS</h4>
                <ul className="space-y-1">
                  {aiNotes.unresolved?.map((u, i) => (
                    <li key={i} className="text-xs">? {u}</li>
                  ))}
                </ul>
              </div>
            )}

            {aiNotes.follow_up_email && (
              <div>
                <h4 className="text-xs font-semibold text-muted-foreground mb-1.5">FOLLOW-UP EMAIL</h4>
                <pre className="text-xs whitespace-pre-wrap bg-muted p-2 rounded-md">{aiNotes.follow_up_email}</pre>
              </div>
            )}

            {generatedAt && (
              <p className="text-xs text-muted-foreground">
                Generated: {new Date(generatedAt).toLocaleString()}
              </p>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function formatNotesForCopy(notes: AiNotesData): string {
  const lines = [
    "AI MEETING NOTES",
    "",
    notes.summary,
  ];
  if (notes.decisions?.length) {
    lines.push("\nDECISIONS", ...notes.decisions?.map(d => `• ${d}`));
  }
  if (notes.action_items?.length) {
    lines.push("\nACTION ITEMS", ...notes.action_items?.map(a => `□ ${a.description}${a.assignee ? ` → ${a.assignee}` : ''}`));
  }
  if (notes.unresolved?.length) {
    lines.push("\nOPEN QUESTIONS", ...notes.unresolved?.map(u => `? ${u}`));
  }
  if (notes.follow_up_email) {
    lines.push("\nFOLLOW-UP EMAIL", notes.follow_up_email);
  }
  return lines.join("\n");
}
