"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { MeetingsList } from "@vexa/dash-meetings-list";
import type { MeetingResponse } from "@vexa/dash-contracts";
import { RefreshCw, Plus } from "lucide-react";
import { useVexa } from "../providers";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

export default function MeetingsPage() {
  const { apiClient } = useVexa();
  const router = useRouter();
  const [meetings, setMeetings] = useState<MeetingResponse[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    let alive = true;
    apiClient
      .getMeetings()
      .then((res) => {
        if (alive) {
          setMeetings(res.meetings || []);
          setLoading(false);
        }
      })
      .catch((e: unknown) => {
        if (alive) {
          setError(e instanceof Error ? e.message : String(e));
          setLoading(false);
        }
      });
    return () => {
      alive = false;
    };
  }, [apiClient]);

  useEffect(() => load(), [load]);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Meetings</h1>
          <p className="text-muted-foreground mt-1 text-sm">
            Bots you&apos;ve sent into meetings — live and past.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={load} disabled={loading}>
            <RefreshCw className="h-4 w-4" /> Refresh
          </Button>
          <Button size="sm" onClick={() => router.push("/join")}>
            <Plus className="h-4 w-4" /> Start a bot
          </Button>
        </div>
      </div>

      <Card>
        <CardContent className="p-0">
          {loading ? (
            <div className="space-y-2 p-4">
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-12 w-full" />
              ))}
            </div>
          ) : error ? (
            // DF3 — a load failure is LOUD (not an empty list): a clear error + retry.
            <div className="p-8 text-center">
              <p className="text-destructive text-sm font-medium">Couldn&apos;t load meetings</p>
              <p className="text-muted-foreground mt-1 text-sm">{error}</p>
              <Button variant="outline" size="sm" className="mt-4" onClick={load}>
                Try again
              </Button>
            </div>
          ) : (
            <MeetingsList
              meetings={meetings}
              onOpen={(m) => router.push(`/meetings/${m.id}`)}
              emptyMessage="No meetings yet — start a bot to begin."
            />
          )}
        </CardContent>
      </Card>
    </div>
  );
}
