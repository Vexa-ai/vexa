"use client";

import { useEffect } from "react";
import { Webhook, Loader2 } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useWebhookStore, type WebhookDeliveryStatus } from "@/stores/webhook-store";
import { useAuthStore } from "@/stores/auth-store";
import { cn } from "@/lib/utils";

function StatusDot({ status }: { status: WebhookDeliveryStatus }) {
  return (
    <span
      className={cn(
        "inline-block w-2 h-2 rounded-full",
        status === "delivered" && "bg-emerald-400",
        status === "retrying" && "bg-amber-400",
        status === "failed" && "bg-red-400"
      )}
    />
  );
}

function StatusBadge({ code }: { code: number | null }) {
  if (code === null) return <span className="inline-flex items-center px-2 py-0.5 rounded text-[11px] font-medium bg-red-900/30 text-red-300">timeout</span>;
  const isSuccess = code >= 200 && code < 300;
  return (
    <span
      className={cn(
        "inline-flex items-center px-2 py-0.5 rounded text-[11px] font-medium",
        isSuccess ? "bg-emerald-900/30 text-emerald-300" : "bg-red-900/30 text-red-300"
      )}
    >
      {code}
    </span>
  );
}

function formatResponseTime(ms: number | null): string {
  if (ms === null) return "30s";
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`;
  return `${ms}ms`;
}

function formatDate(dateStr: string): string {
  const d = new Date(dateStr);
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" }) +
    ", " +
    d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: false });
}

export default function WebhooksPage() {
  const user = useAuthStore((state) => state.user);
  const {
    deliveries,
    stats,
    isLoading,
    statusFilter,
    timeRange,
    setStatusFilter,
    setTimeRange,
    setUserId,
    fetchDeliveries,
  } = useWebhookStore();

  useEffect(() => {
    if (user?.id) {
      setUserId(user.id);
    }
  }, [user?.id, setUserId]);

  useEffect(() => {
    if (user?.id) {
      fetchDeliveries();
    }
  }, [user?.id, fetchDeliveries]);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-[-0.02em] text-foreground">
            Webhook History
          </h1>
          <p className="text-sm text-muted-foreground">
            Monitor webhook delivery status and retry attempts
          </p>
        </div>
        <div className="flex items-center gap-3">
          <Select
            value={statusFilter}
            onValueChange={(v) => setStatusFilter(v as WebhookDeliveryStatus | "all")}
          >
            <SelectTrigger className="w-[150px]">
              <SelectValue placeholder="All statuses" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All statuses</SelectItem>
              <SelectItem value="delivered">Delivered</SelectItem>
              <SelectItem value="retrying">Retrying</SelectItem>
              <SelectItem value="failed">Failed</SelectItem>
            </SelectContent>
          </Select>
          <Select
            value={timeRange}
            onValueChange={(v) => setTimeRange(v as "24h" | "7d" | "30d")}
          >
            <SelectTrigger className="w-[150px]">
              <SelectValue placeholder="Last 7 days" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="24h">Last 24 hours</SelectItem>
              <SelectItem value="7d">Last 7 days</SelectItem>
              <SelectItem value="30d">Last 30 days</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
        <Card>
          <CardContent className="pt-4 pb-4">
            <p className="text-xs text-muted-foreground mb-0.5">Total</p>
            <p className="text-xl font-semibold">{stats.total}</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4 pb-4">
            <p className="text-xs text-muted-foreground mb-0.5">Delivered</p>
            <p className="text-xl font-semibold text-emerald-400">{stats.delivered}</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4 pb-4">
            <p className="text-xs text-muted-foreground mb-0.5">Retrying</p>
            <p className="text-xl font-semibold text-amber-400">{stats.retrying}</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4 pb-4">
            <p className="text-xs text-muted-foreground mb-0.5">Failed</p>
            <p className="text-xl font-semibold text-red-400">{stats.failed}</p>
          </CardContent>
        </Card>
      </div>

      {/* Deliveries table */}
      <Card className="overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="border-b text-xs text-muted-foreground uppercase tracking-wider">
                <th className="text-left px-5 py-3 font-medium">Event</th>
                <th className="text-left px-5 py-3 font-medium">Meeting</th>
                <th className="text-left px-5 py-3 font-medium">Status</th>
                <th className="text-left px-5 py-3 font-medium">Attempts</th>
                <th className="text-left px-5 py-3 font-medium">Response</th>
                <th className="text-left px-5 py-3 font-medium">Time</th>
              </tr>
            </thead>
            <tbody className="text-sm">
              {isLoading ? (
                <tr>
                  <td colSpan={6} className="px-5 py-12 text-center">
                    <Loader2 className="h-5 w-5 animate-spin mx-auto text-muted-foreground" />
                  </td>
                </tr>
              ) : deliveries.length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-5 py-12 text-center">
                    <div className="flex flex-col items-center gap-2">
                      <Webhook className="h-8 w-8 text-muted-foreground/50" />
                      <p className="text-sm text-muted-foreground">
                        No webhook deliveries found
                      </p>
                      <p className="text-xs text-muted-foreground/70">
                        Configure a webhook endpoint in your profile settings to start receiving events.
                      </p>
                    </div>
                  </td>
                </tr>
              ) : (
                deliveries.map((delivery) => (
                  <tr
                    key={delivery.id}
                    className="border-b border-border/50 hover:bg-muted/30 cursor-pointer transition-colors"
                  >
                    <td className="px-5 py-3">
                      <span className="font-mono text-xs text-muted-foreground">
                        {delivery.event}
                      </span>
                    </td>
                    <td className="px-5 py-3 font-medium">{delivery.meeting_name}</td>
                    <td className="px-5 py-3">
                      <span className="inline-flex items-center gap-1.5">
                        <StatusDot status={delivery.status} />
                        <span
                          className={cn(
                            "text-xs",
                            delivery.status === "delivered" && "text-emerald-400",
                            delivery.status === "retrying" && "text-amber-400",
                            delivery.status === "failed" && "text-red-400"
                          )}
                        >
                          {delivery.status}
                        </span>
                      </span>
                    </td>
                    <td className="px-5 py-3 text-muted-foreground">
                      {delivery.attempts}/{delivery.max_attempts}
                    </td>
                    <td className="px-5 py-3">
                      <StatusBadge code={delivery.response_status} />{" "}
                      <span className="text-xs text-muted-foreground">
                        {formatResponseTime(delivery.response_time_ms)}
                      </span>
                    </td>
                    <td className="px-5 py-3 text-muted-foreground text-xs">
                      {formatDate(delivery.last_attempt_at)}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
