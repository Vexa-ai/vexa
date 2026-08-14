"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { Bot, CalendarDays, Clock3, Loader2, Plus, RefreshCw, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { calendarAPI, type CalendarConnection, type CalendarSyncStamp } from "@/lib/calendar-api";
import { vexaAPI } from "@/lib/api";
import type { Meeting } from "@/types/vexa";

function SyncStatus({ stamp }: { stamp?: CalendarSyncStamp }) {
  if (!stamp?.last_sync) return <span className="text-xs text-muted-foreground">Not synced yet</span>;
  if (stamp.last_error) return <span className="text-xs text-red-400">{stamp.last_error}</span>;
  const counts = stamp.counts;
  return (
    <span className="text-xs text-muted-foreground">
      Synced {new Date(stamp.last_sync).toLocaleString()}
      {counts ? ` · ${counts.created} imported, ${counts.updated} updated` : ""}
    </span>
  );
}

export default function CalendarPage() {
  const [calendars, setCalendars] = useState<CalendarConnection[]>([]);
  const [stamps, setStamps] = useState<Record<string, CalendarSyncStamp>>({});
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [autoJoin, setAutoJoin] = useState(true);
  const [botName, setBotName] = useState("Vexa");
  const [savedBotName, setSavedBotName] = useState("Vexa");
  const [upcoming, setUpcoming] = useState<Meeting[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [items, preferences, scheduled] = await Promise.all([
        calendarAPI.list(),
        calendarAPI.preferences(),
        vexaAPI.getMeetings({ status: "scheduled", limit: 100 }),
      ]);
      setCalendars(items);
      setBotName(preferences.bot_name || "Vexa");
      setSavedBotName(preferences.bot_name || "Vexa");
      const now = Date.now();
      setUpcoming(scheduled.meetings
        .filter((meeting) => {
          const at = typeof meeting.data.scheduled_at === "string"
            ? Date.parse(meeting.data.scheduled_at)
            : Number.NaN;
          return Number.isFinite(at)
            && at >= now
            && meeting.data.auto_join !== false
            && Array.isArray(meeting.data.calendar_sources)
            && meeting.data.calendar_sources.length > 0;
        })
        .sort((a, b) => Date.parse(String(a.data.scheduled_at)) - Date.parse(String(b.data.scheduled_at))));
      const entries = await Promise.all(items.map(async (calendar) => {
        try {
          const status = await calendarAPI.status(calendar.id);
          return [calendar.id, status] as const;
        } catch {
          return [calendar.id, {}] as const;
        }
      }));
      setStamps(Object.fromEntries(entries) as Record<string, CalendarSyncStamp>);
    } catch (error) {
      toast.error("Could not load calendars", { description: (error as Error).message });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  const saveBotName = async () => {
    const value = botName.trim();
    if (!value) return;
    setBusy("bot-name");
    try {
      const preferences = await calendarAPI.updatePreferences({ bot_name: value });
      setBotName(preferences.bot_name);
      setSavedBotName(preferences.bot_name);
      toast.success("Default bot name saved");
    } catch (error) {
      toast.error("Could not save bot name", { description: (error as Error).message });
    } finally {
      setBusy(null);
    }
  };

  const addCalendar = async (event: FormEvent) => {
    event.preventDefault();
    setBusy("new");
    try {
      const created = await calendarAPI.create({ name: name.trim(), ics_url: url.trim(), auto_join: autoJoin });
      setUrl("");
      setName("");
      try {
        const stamp = await calendarAPI.sync(created.id);
        setStamps((current) => ({ ...current, [created.id]: stamp }));
        toast.success("Calendar connected", { description: "The first sync completed." });
      } catch (error) {
        toast.warning("Calendar saved, but sync needs attention", { description: (error as Error).message });
      }
      await refresh();
    } catch (error) {
      toast.error("Could not connect calendar", { description: (error as Error).message });
    } finally {
      setBusy(null);
    }
  };

  const sync = async (calendar: CalendarConnection) => {
    setBusy(`sync:${calendar.id}`);
    try {
      const stamp = await calendarAPI.sync(calendar.id);
      setStamps((current) => ({ ...current, [calendar.id]: stamp }));
      if (stamp.last_error) toast.error("Sync failed", { description: stamp.last_error });
      else toast.success(`${calendar.name} synced`);
    } catch (error) {
      toast.error("Sync failed", { description: (error as Error).message });
    } finally {
      setBusy(null);
    }
  };

  const toggleAutoJoin = async (calendar: CalendarConnection, checked: boolean) => {
    setBusy(`update:${calendar.id}`);
    try {
      const updated = await calendarAPI.update(calendar.id, { auto_join: checked });
      setCalendars((items) => items.map((item) => item.id === updated.id ? updated : item));
      try {
        const stamp = await calendarAPI.sync(calendar.id);
        setStamps((current) => ({ ...current, [calendar.id]: stamp }));
      } catch (error) {
        toast.warning("Auto-join preference saved, but sync needs attention", {
          description: (error as Error).message,
        });
      }
    } catch (error) {
      toast.error("Could not update calendar", { description: (error as Error).message });
    } finally {
      setBusy(null);
    }
  };

  const disconnect = async (calendar: CalendarConnection) => {
    if (!window.confirm(`Disconnect ${calendar.name}? Imported meetings owned only by this calendar will be removed.`)) return;
    setBusy(`delete:${calendar.id}`);
    try {
      await calendarAPI.disconnect(calendar.id);
      // The retained secret-free tombstone lets meeting-api remove this source immediately.
      try { await calendarAPI.sync(calendar.id); } catch { /* background sync retries cleanup */ }
      setCalendars((items) => items.filter((item) => item.id !== calendar.id));
      toast.success(`${calendar.name} disconnected`);
    } catch (error) {
      toast.error("Could not disconnect calendar", { description: (error as Error).message });
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-[-0.02em] text-foreground">Calendar</h1>
        <p className="text-sm text-muted-foreground">
          Connect multiple ICS feeds and control which meetings Vexa joins.{" "}
          <a
            href="https://docs.vexa.ai/how-to/calendar-sync"
            target="_blank"
            rel="noreferrer"
            className="font-medium text-foreground underline underline-offset-4 hover:text-primary"
          >
            API &amp; setup docs
          </a>
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><Plus className="h-5 w-5" /> Add calendar</CardTitle>
          <CardDescription>Use the secret iCal/ICS address from Google Calendar or Outlook. Treat it like a password.</CardDescription>
          <div className="space-y-1.5 rounded-md border border-border/60 bg-muted/30 p-3 text-sm text-muted-foreground">
            <p>
              <span className="font-medium text-foreground">Google Calendar:</span>{" "}
              Settings → choose your calendar → Integrate calendar → copy <span className="font-medium text-foreground">Secret address in iCal format</span>.
            </p>
            <p>
              <span className="font-medium text-foreground">Outlook:</span>{" "}
              Settings → Calendar → Shared calendars → Publish a calendar → copy the <span className="font-medium text-foreground">ICS</span> link.
            </p>
            <p>
              Do not paste a public calendar page or embed link.{" "}
              <a
                href="https://docs.vexa.ai/how-to/calendar-sync#1-find-your-secret-ics-address"
                target="_blank"
                rel="noreferrer"
                className="font-medium text-foreground underline underline-offset-4 hover:text-primary"
              >
                Detailed instructions
              </a>
            </p>
          </div>
        </CardHeader>
        <CardContent>
          <form onSubmit={addCalendar} className="grid gap-4 md:grid-cols-[minmax(10rem,0.5fr)_minmax(18rem,1fr)_auto] md:items-end">
            <div className="space-y-1.5">
              <Label htmlFor="calendar-name">Name</Label>
              <Input id="calendar-name" value={name} onChange={(event) => setName(event.target.value)} placeholder="Work" maxLength={100} required />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="calendar-url">Secret ICS address</Label>
              <Input id="calendar-url" type="password" autoComplete="off" value={url} onChange={(event) => setUrl(event.target.value)} placeholder="https://calendar.google.com/…/basic.ics" required />
            </div>
            <Button type="submit" disabled={busy === "new" || !name.trim() || !url.trim()}>
              {busy === "new" ? <Loader2 className="h-4 w-4 animate-spin" /> : "Connect"}
            </Button>
            <label className="md:col-span-3 flex items-center gap-2 text-sm text-muted-foreground">
              <Switch checked={autoJoin} onCheckedChange={setAutoJoin} /> Auto-join meetings imported from this calendar
            </label>
          </form>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><Bot className="h-5 w-5" /> Auto-join settings</CardTitle>
          <CardDescription>This name is used by every bot that joins from your connected calendars.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-end">
          <div className="space-y-1.5">
            <Label htmlFor="calendar-bot-name">Default bot name</Label>
            <Input id="calendar-bot-name" value={botName} onChange={(event) => setBotName(event.target.value)} maxLength={100} placeholder="Vexa" />
          </div>
          <Button type="button" variant="secondary" onClick={() => void saveBotName()} disabled={busy === "bot-name" || !botName.trim() || botName.trim() === savedBotName}>
            {busy === "bot-name" ? <Loader2 className="h-4 w-4 animate-spin" /> : "Save"}
          </Button>
        </CardContent>
      </Card>

      <div className="space-y-3">
        <div>
          <h2 className="text-lg font-medium">Upcoming auto-joins</h2>
          <p className="text-sm text-muted-foreground">Meetings imported from your calendars that Vexa is scheduled to join.</p>
        </div>
        {loading ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" /> Loading meetings…</div>
        ) : upcoming.length === 0 ? (
          <Card><CardContent className="py-8 text-center text-sm text-muted-foreground">No upcoming auto-joins found.</CardContent></Card>
        ) : upcoming.map((meeting) => {
          const sources = meeting.data.calendar_sources ?? [];
          return (
            <Card key={meeting.id}>
              <CardContent className="flex flex-col gap-3 py-5 sm:flex-row sm:items-center">
                <Clock3 className="h-5 w-5 shrink-0 text-muted-foreground" />
                <div className="min-w-0 flex-1">
                  <p className="truncate font-medium">{meeting.data.title || meeting.data.name || "Untitled meeting"}</p>
                  <p className="text-sm text-muted-foreground">{new Date(String(meeting.data.scheduled_at)).toLocaleString()}</p>
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {sources.map((source) => (
                    <span key={`${meeting.id}:${source.id}:${source.uid}`} className="rounded-full bg-muted px-2.5 py-1 text-xs text-muted-foreground">{source.name}</span>
                  ))}
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>

      <div className="space-y-3">
        <h2 className="text-lg font-medium">Connected calendars</h2>
        {loading ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" /> Loading calendars…</div>
        ) : calendars.length === 0 ? (
          <Card><CardContent className="py-10 text-center text-sm text-muted-foreground">No calendars connected yet.</CardContent></Card>
        ) : calendars.map((calendar) => (
          <Card key={calendar.id}>
            <CardContent className="flex flex-col gap-4 py-5 md:flex-row md:items-center">
              <div className="flex min-w-0 flex-1 items-start gap-3">
                <CalendarDays className="mt-0.5 h-5 w-5 shrink-0 text-muted-foreground" />
                <div className="min-w-0">
                  <p className="font-medium">{calendar.name}</p>
                  <p className="truncate font-mono text-xs text-muted-foreground">{calendar.ics_url_masked}</p>
                  <SyncStatus stamp={stamps[calendar.id]} />
                </div>
              </div>
              <label className="flex items-center gap-2 text-sm text-muted-foreground">
                <Switch checked={calendar.auto_join} disabled={busy === `update:${calendar.id}`} onCheckedChange={(checked) => void toggleAutoJoin(calendar, checked)} />
                Auto-join
              </label>
              <div className="flex gap-2">
                <Button variant="secondary" onClick={() => void sync(calendar)} disabled={busy === `sync:${calendar.id}`}>
                  {busy === `sync:${calendar.id}` ? <Loader2 className="h-4 w-4 animate-spin" /> : <><RefreshCw className="mr-2 h-4 w-4" /> Sync now</>}
                </Button>
                <Button variant="outline" className="text-destructive" onClick={() => void disconnect(calendar)} disabled={busy === `delete:${calendar.id}`}>
                  <Trash2 className="mr-2 h-4 w-4" /> Disconnect
                </Button>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
