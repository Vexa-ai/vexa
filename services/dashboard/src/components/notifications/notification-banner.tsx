"use client";

import { useState, useEffect } from "react";
import { Bell, X } from "lucide-react";
import { cn } from "@/lib/utils";

interface Notification {
  id: string;
  type: "maintenance" | "incident" | "announcement";
  title: string;
  body: string;
  link?: string;
  active_from: string;
  active_until: string;
}

const DISMISSED_KEY = "dismissed-notifications";
const BLOG_URL =
  process.env.NEXT_PUBLIC_BLOG_URL || "https://blog.vexa.ai";

function getDismissedIds(): string[] {
  if (typeof window === "undefined") return [];
  try {
    return JSON.parse(localStorage.getItem(DISMISSED_KEY) || "[]");
  } catch {
    return [];
  }
}

const typeConfig = {
  maintenance: {
    bg: "bg-amber-500/10",
    icon: "text-amber-400",
    text: "text-amber-200",
    body: "text-amber-200/70",
    link: "text-amber-300 hover:text-amber-100",
  },
  incident: {
    bg: "bg-red-500/10",
    icon: "text-red-400",
    text: "text-red-200",
    body: "text-red-200/70",
    link: "text-red-300 hover:text-red-100",
  },
  announcement: {
    bg: "bg-sky-500/10",
    icon: "text-sky-400",
    text: "text-sky-200",
    body: "text-sky-200/70",
    link: "text-sky-300 hover:text-sky-100",
  },
} as const;

export function NotificationBanner() {
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [dismissed, setDismissed] = useState<string[]>([]);

  useEffect(() => {
    setDismissed(getDismissedIds());

    async function fetchNotifications() {
      try {
        const resp = await fetch(`${BLOG_URL}/notifications.json`, {
          cache: "no-store",
        });
        if (!resp.ok) return;
        const data: Notification[] = await resp.json();
        const now = new Date();
        const active = data.filter((n) => {
          const from = new Date(n.active_from);
          const until = new Date(n.active_until);
          return now >= from && now <= until;
        });
        setNotifications(active);
      } catch {
        // Notifications are optional — fail silently
      }
    }
    fetchNotifications();
  }, []);

  function dismiss(id: string) {
    const updated = [...dismissed, id];
    setDismissed(updated);
    try {
      localStorage.setItem(DISMISSED_KEY, JSON.stringify(updated));
    } catch {}
  }

  const visible = notifications.filter((n) => !dismissed.includes(n.id));
  if (visible.length === 0) return null;

  return (
    <div className="space-y-2">
      {visible.map((n) => {
        const s = typeConfig[n.type] || typeConfig.announcement;
        return (
          <div
            key={n.id}
            className={cn("rounded-lg px-4 py-3 flex items-center gap-3", s.bg)}
          >
            <Bell className={cn("h-4 w-4 flex-shrink-0", s.icon)} />
            <p className="flex-1 text-sm">
              <span className={cn("font-medium", s.text)}>{n.title}</span>
              {n.body && (
                <span className={cn("ml-1", s.body)}>{n.body}</span>
              )}
              {n.link && (
                <a
                  href={n.link}
                  target="_blank"
                  rel="noopener noreferrer"
                  className={cn(
                    "ml-1 underline underline-offset-2",
                    s.link
                  )}
                >
                  Read more
                </a>
              )}
            </p>
            <button
              onClick={() => dismiss(n.id)}
              className="flex-shrink-0 p-1 rounded-md text-muted-foreground/40 hover:text-foreground transition-opacity"
              aria-label="Dismiss"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        );
      })}
    </div>
  );
}
