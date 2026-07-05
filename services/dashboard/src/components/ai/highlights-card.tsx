"use client";

import { useEffect, useState } from "react";
import { Sparkles, Plus, Video, Clock } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { withBasePath } from "@/lib/base-path";

interface HighlightData {
  id?: number;
  meeting_id?: number | string;
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
  const [items, setItems] = useState<HighlightData[] | null>(null);
  const [adding, setAdding] = useState(false);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [title, setTitle] = useState("");

  useEffect(() => {
    if (!meetingId) return;
    let cancelled = false;
    fetch(withBasePath(`/api/vexa/internal/meetings/${meetingId}/highlights`))
      .then(r => r.ok ? r.json() : [])
      .then(data => {
        if (!cancelled) setItems(Array.isArray(data) ? data : []);
      })
      .catch(() => {
        if (!cancelled) setItems(null);
      });
    return () => {
      cancelled = true;
    };
  }, [meetingId]);

  const addHighlight = async (startTime: number, endTime: number, value: string) => {
    if (onAdd) {
      onAdd(startTime, endTime, value);
      return;
    }
    if (!meetingId) return;
    const res = await fetch(withBasePath(`/api/vexa/internal/meetings/${meetingId}/highlights`), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        start_time: startTime,
        end_time: endTime,
        title: value || "Untitled highlight",
        source: "manual",
      }),
    });
    if (!res.ok) throw new Error("Failed to create highlight");
    const created = await res.json();
    setItems(current => [...(current || highlights || []), created].sort((a, b) => (a.start_time || 0) - (b.start_time || 0)));
  };

  const submitHighlight = async () => {
    const s = parseTime(start);
    const e = parseTime(end);
    if (s !== null && e !== null) await addHighlight(s, e, title);
    setAdding(false);
    setStart("");
    setEnd("");
    setTitle("");
  };

  const visibleItems = items ?? highlights ?? [];

  if (visibleItems.length === 0) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <div className="flex items-center justify-between">
            <CardTitle className="flex items-center gap-2 text-sm">
              <Sparkles className="h-4 w-4 text-primary" />
              Highlights
            </CardTitle>
            <Button variant="ghost" size="sm" className="h-7 w-7 p-0" onClick={() => setAdding(true)} disabled={!meetingId}>
              <Plus className="h-3.5 w-3.5" />
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            No highlights yet. Click + to create one from the transcript.
          </p>
          {adding && (
            <HighlightForm
              start={start}
              end={end}
              title={title}
              setStart={setStart}
              setEnd={setEnd}
              setTitle={setTitle}
              onCancel={() => setAdding(false)}
              onSubmit={submitHighlight}
            />
          )}
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
            Highlights ({visibleItems.length})
          </CardTitle>
          <Button variant="ghost" size="sm" className="h-7 w-7 p-0" onClick={() => setAdding(true)} disabled={!meetingId}>
            <Plus className="h-3.5 w-3.5" />
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-2">
        {visibleItems.map(h => (
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
          <HighlightForm
            start={start}
            end={end}
            title={title}
            setStart={setStart}
            setEnd={setEnd}
            setTitle={setTitle}
            onCancel={() => setAdding(false)}
            onSubmit={submitHighlight}
          />
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

function HighlightForm({
  start,
  end,
  title,
  setStart,
  setEnd,
  setTitle,
  onCancel,
  onSubmit,
}: {
  start: string;
  end: string;
  title: string;
  setStart: (value: string) => void;
  setEnd: (value: string) => void;
  setTitle: (value: string) => void;
  onCancel: () => void;
  onSubmit: () => void | Promise<void>;
}) {
  return (
    <div className="p-2 rounded-md border">
      <input className="w-full text-sm p-1 border rounded mb-1" placeholder="Start (mm:ss)" value={start} onChange={e => setStart(e.target.value)} />
      <input className="w-full text-sm p-1 border rounded mb-1" placeholder="End (mm:ss)" value={end} onChange={e => setEnd(e.target.value)} />
      <input className="w-full text-sm p-1 border rounded mb-1" placeholder="Title" value={title} onChange={e => setTitle(e.target.value)} />
      <div className="flex gap-2">
        <Button size="sm" onClick={() => void onSubmit()}>Add</Button>
        <Button variant="ghost" size="sm" onClick={onCancel}>Cancel</Button>
      </div>
    </div>
  );
}
