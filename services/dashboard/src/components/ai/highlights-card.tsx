"use client";

import { useState } from "react";
import { Sparkles, Copy, Check, Plus, Video, Clock } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

interface HighlightData {
  id?: number;
  meeting_id?: number;
  start_time?: number;
  end_time?: number;
  title?: string;
  summary?: string;
  type?: string;
  speaker?: string;
  clip_token?: string;
}

interface HighlightsCardProps {
  highlights?: HighlightData[];
  meetingId?: string | number;
  onAdd?: (start: number, end: number, title: string) => void;
}

export function HighlightsCard({ highlights, meetingId, onAdd }: HighlightsCardProps) {
  const [adding, setAdding] = useState(false);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [title, setTitle] = useState("");

  if (!highlights || highlights.length === 0) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <div className="flex items-center justify-between">
            <CardTitle className="flex items-center gap-2 text-sm">
              <Sparkles className="h-4 w-4 text-primary" />
              Highlights
            </CardTitle>
            <Button variant="ghost" size="sm" className="h-7 w-7 p-0" onClick={() => setAdding(true)}>
              <Plus className="h-3.5 w-3.5" />
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            No highlights yet. Click + to create one from the transcript.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="flex items-center gap-2 text-sm">
            <Sparkles className="h-4 w-4 text-primary" />
            Highlights ({highlights.length})
          </CardTitle>
          <Button variant="ghost" size="sm" className="h-7 w-7 p-0" onClick={() => setAdding(true)}>
            <Plus className="h-3.5 w-3.5" />
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-2">
        {highlights.map(h => (
          <div key={h.id} className="text-sm p-2 rounded-md bg-muted/50">
            <div className="flex items-center gap-2">
              <Clock className="h-3 w-3 text-muted-foreground" />
              <span className="text-xs text-muted-foreground">
                {Math.floor((h.start_time ?? 0) / 60)}:{String(Math.floor((h.start_time ?? 0) % 60)).padStart(2, '0')}
                {' - '}
                {Math.floor((h.end_time ?? 0) / 60)}:{String(Math.floor((h.end_time ?? 0) % 60)).padStart(2, '0')}
              </span>
              {h.speaker && <Badge variant="secondary" className="text-xs">{h.speaker}</Badge>}
              {h.clip_token && <Video className="h-3 w-3 text-green-500" />}
            </div>
            <p className="mt-1">{h.title || h.summary || "Untitled highlight"}</p>
          </div>
        ))}
        {adding && (
          <div className="p-2 rounded-md border">
            <input className="w-full text-sm p-1 border rounded mb-1" placeholder="Start (mm:ss)" value={start} onChange={e => setStart(e.target.value)} />
            <input className="w-full text-sm p-1 border rounded mb-1" placeholder="End (mm:ss)" value={end} onChange={e => setEnd(e.target.value)} />
            <input className="w-full text-sm p-1 border rounded mb-1" placeholder="Title" value={title} onChange={e => setTitle(e.target.value)} />
            <div className="flex gap-2">
              <Button size="sm" onClick={() => {
                const s = parseTime(start);
                const e = parseTime(end);
                if (s && e && onAdd) onAdd(s, e, title);
                setAdding(false); setStart(""); setEnd(""); setTitle("");
              }}>Add</Button>
              <Button variant="ghost" size="sm" onClick={() => setAdding(false)}>Cancel</Button>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function parseTime(s: string): number | null {
  const parts = s.split(':');
  if (parts.length !== 2) return null;
  const m = parseInt(parts[0], 10);
  const sec = parseInt(parts[1], 10);
  if (isNaN(m) || isNaN(sec)) return null;
  return m * 60 + sec;
}
