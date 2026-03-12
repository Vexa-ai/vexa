"use client";

import { useState, useEffect } from "react";
import { Bell, X } from "lucide-react";
import { cn } from "@/lib/utils";

interface Notification {
  id: string;
  type: "maintenance" | "incident" | "announcement";
  title: string;
  message: string;
  link?: string;
  link_text?: string;
}

const DISMISSED_KEY = "vexa-dismissed-notifications";

function getDismissedIds(): string[] {
  if (typeof window === "undefined") return [];
  try {
    return JSON.parse(localStorage.getItem(DISMISSED_KEY) || "[]");
  } catch {
    return [];
  }
}

function dismissNotification(id: string) {
  const dismissed = getDismissedIds();
  if (!dismissed.includes(id)) {
    dismissed.push(id);
    localStorage.setItem(DISMISSED_KEY, JSON.stringify(dismissed));
  }
}

export function NotificationBanner() {
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [dismissed, setDismissed] = useState<string[]>([]);

  useEffect(() => {
    setDismissed(getDismissedIds());

    // Fetch notifications from static JSON or blog API
    async function fetchNotifications() {
      try {
        const response = await fetch("/api/notifications");
        if (!response.ok) return;
        const data = await response.json();
        setNotifications(data.notifications || []);
      } catch {
        // Silent fail — notifications are non-critical
      }
    }
    fetchNotifications();
  }, []);

  const visible = notifications.filter((n) => !dismissed.includes(n.id));
  if (visible.length === 0) return null;

  return (
    <div className="space-y-2">
      {visible.map((notification) => (
        <div
          key={notification.id}
          className={cn(
            "rounded-lg border px-4 py-3 flex items-center justify-between",
            notification.type === "maintenance" &&
              "border-amber-800/30 bg-amber-950/20",
            notification.type === "incident" &&
              "border-red-800/30 bg-red-950/20",
            notification.type === "announcement" &&
              "border-blue-800/30 bg-blue-950/20"
          )}
        >
          <div className="flex items-center gap-3">
            <Bell
              className={cn(
                "h-4 w-4 flex-shrink-0",
                notification.type === "maintenance" && "text-amber-400",
                notification.type === "incident" && "text-red-400",
                notification.type === "announcement" && "text-blue-400"
              )}
            />
            <p
              className={cn(
                "text-sm",
                notification.type === "maintenance" && "text-amber-300",
                notification.type === "incident" && "text-red-300",
                notification.type === "announcement" && "text-blue-300"
              )}
            >
              {notification.title}
              {notification.message && (
                <>
                  {" "}
                  <span className="opacity-60">{notification.message}</span>
                </>
              )}
              {notification.link && (
                <>
                  {" "}
                  <a
                    href={notification.link}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="underline hover:opacity-80"
                  >
                    {notification.link_text || "Details"}
                  </a>
                </>
              )}
            </p>
          </div>
          <button
            onClick={() => {
              dismissNotification(notification.id);
              setDismissed((prev) => [...prev, notification.id]);
            }}
            className="text-xs text-muted-foreground hover:text-foreground ml-4 flex-shrink-0"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      ))}
    </div>
  );
}
