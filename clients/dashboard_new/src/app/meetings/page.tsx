"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { MeetingsList } from "@vexa/dash-meetings-list";
import type { MeetingResponse } from "@vexa/dash-contracts";
import { useVexa } from "../providers";

export default function MeetingsPage() {
  const { apiClient } = useVexa();
  const router = useRouter();
  const [meetings, setMeetings] = useState<MeetingResponse[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    apiClient
      .getMeetings()
      .then((res) => {
        if (!alive) return;
        setMeetings(res.meetings || []);
        setLoading(false);
      })
      .catch((e: unknown) => {
        if (!alive) return;
        setError(e instanceof Error ? e.message : String(e));
        setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [apiClient]);

  return (
    <div>
      <div className="panel">
        <h2>Meetings</h2>
        {loading && <p className="muted">Loading…</p>}
        {error && <p style={{ color: "var(--bad)" }}>Failed to load meetings: {error}</p>}
        {!loading && !error && (
          <MeetingsList
            meetings={meetings}
            onOpen={(m) => router.push(`/meetings/${m.id}`)}
            emptyMessage="No meetings yet — start a bot to begin."
          />
        )}
      </div>
    </div>
  );
}
