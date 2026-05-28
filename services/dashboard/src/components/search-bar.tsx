"use client";

import { useState, useRef, useEffect } from "react";
import { Search, X, Clock, Users, FileText } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

interface SearchResult {
  meeting: {
    id: number;
    platform: string;
    native_id: string;
    status: string;
    start_time?: string;
    data?: Record<string, any>;
  };
  matched_segments: Array<{
    text: string;
    speaker: string;
    timestamp: string;
  }>;
}

interface SearchBarProps {
  onSearch?: (query: string) => Promise<SearchResult[]>;
}

export function SearchBar({ onSearch }: SearchBarProps) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [active, setActive] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setActive(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const handleSearch = async () => {
    if (!query.trim() || !onSearch) return;
    setLoading(true);
    setActive(true);
    try {
      const resp = await onSearch(query);
      setResults(resp);
    } catch (err) {
      console.error("Search failed:", err);
    }
    setLoading(false);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") handleSearch();
    if (e.key === "Escape") {
      setQuery("");
      setActive(false);
    }
  };

  return (
    <div ref={ref} className="relative w-full">
      <div className="flex gap-2">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <input
            type="text"
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            onFocus={() => query && setActive(true)}
            placeholder="Search meetings, transcripts, highlights..."
            className="w-full pl-9 pr-3 py-2 text-sm border rounded-md bg-background"
          />
          {query && (
            <button
              onClick={() => { setQuery(""); setActive(false); }}
              className="absolute right-2 top-1/2 -translate-y-1/2"
            >
              <X className="h-3.5 w-3.5 text-muted-foreground" />
            </button>
          )}
        </div>
        <button
          onClick={handleSearch}
          disabled={!query.trim() || loading}
          className="px-3 py-2 text-sm bg-primary text-primary-foreground rounded-md disabled:opacity-50"
        >
          {loading ? "Searching..." : "Search"}
        </button>
      </div>

      {active && results.length > 0 && (
        <div className="absolute top-full left-0 right-0 mt-1 bg-popover border rounded-md shadow-lg max-h-[600px] overflow-auto z-50">
          {results.map(r => (
            <div key={r.meeting.id} className="p-3 border-b last:border-0">
              <div className="flex items-center gap-2 mb-1">
                <Badge variant="secondary" className="text-xs">{r.meeting.platform}</Badge>
                <span className="text-xs text-muted-foreground">{r.meeting.native_id}</span>
                {r.meeting.start_time && (
                  <Clock className="h-3 w-3 text-muted-foreground" />
                )}
              </div>
              {r.matched_segments.slice(0, 2).map((seg, i) => (
                <div key={i} className="text-xs text-muted-foreground mb-1">
                  <span className="text-muted-foreground">{seg.timestamp}</span>
                  {seg.speaker && <span className="font-medium"> {seg.speaker}:</span>}
                  <span> {seg.text.substring(0, 120)}{seg.text.length > 120 ? "..." : ""}</span>
                </div>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
