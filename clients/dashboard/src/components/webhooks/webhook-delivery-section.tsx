"use client";

import { useEffect } from "react";
import { Webhook, Loader2 } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useWebhookStore } from "@/stores/webhook-store";
import { cn } from "@/lib/utils";

interface WebhookDeliverySectionProps {
  meetingId: string;
}

export function WebhookDeliverySection({ meetingId }: WebhookDeliverySectionProps) {
  const { meetingDeliveries, isLoadingMeetingDeliveries, fetchMeetingDeliveries } =
    useWebhookStore();

  useEffect(() => {
    fetchMeetingDeliveries(meetingId);
  }, [meetingId, fetchMeetingDeliveries]);

  // Don't render anything if no deliveries and not loading
  if (!isLoadingMeetingDeliveries && meetingDeliveries.length === 0) {
    return null;
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Webhook className="h-4 w-4" />
          Webhook Delivery
        </CardTitle>
      </CardHeader>
      <CardContent>
        {isLoadingMeetingDeliveries ? (
          <div className="flex items-center justify-center py-4">
            <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
          </div>
        ) : (
          <div className="rounded-lg bg-muted/50 divide-y divide-border/50 overflow-hidden">
            {meetingDeliveries.map((attempt) => (
              <div
                key={attempt.attempt}
                className="px-4 py-3 flex items-center justify-between"
              >
                <div className="flex items-center gap-3">
                  <span
                    className={cn(
                      "w-2 h-2 rounded-full",
                      attempt.success ? "bg-emerald-400" : "bg-red-400"
                    )}
                  />
                  <div>
                    <p className="text-sm">
                      Attempt {attempt.attempt}{" "}
                      <span className="text-muted-foreground">
                        {new Date(attempt.timestamp).toLocaleString("en-US", {
                          month: "short",
                          day: "numeric",
                          hour: "2-digit",
                          minute: "2-digit",
                          second: "2-digit",
                          hour12: false,
                        })}
                      </span>
                    </p>
                    <p className="text-[11px] font-mono text-muted-foreground mt-0.5">
                      POST {attempt.endpoint_url}
                    </p>
                  </div>
                </div>
                <div className="text-right">
                  <span
                    className={cn(
                      "inline-flex items-center px-2 py-0.5 rounded text-[11px] font-medium",
                      attempt.success
                        ? "bg-emerald-900/30 text-emerald-300"
                        : "bg-red-900/30 text-red-300"
                    )}
                  >
                    {attempt.response_status ?? "timeout"}
                  </span>
                  <p className="text-[11px] text-muted-foreground mt-0.5">
                    {attempt.response_time_ms
                      ? attempt.response_time_ms >= 1000
                        ? `${(attempt.response_time_ms / 1000).toFixed(1)}s`
                        : `${attempt.response_time_ms}ms`
                      : "30s"}
                  </p>
                </div>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
