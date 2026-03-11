"use client";

import { usePathname } from "next/navigation";
import Link from "next/link";
import { BookOpen } from "lucide-react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";

const docsNav = [
  {
    title: "Getting Started",
    items: [
      { title: "Overview", href: "/docs" },
      { title: "Authentication", href: "/docs/auth" },
    ],
  },
  {
    title: "REST API",
    items: [
      { title: "Meetings", href: "/docs/rest/meetings" },
      { title: "Bots", href: "/docs/rest/bots" },
      { title: "Transcripts", href: "/docs/rest/transcripts" },
    ],
  },
  {
    title: "WebSocket API",
    items: [
      { title: "Overview", href: "/docs/ws" },
      { title: "Subscribe", href: "/docs/ws/subscribe" },
      { title: "Events", href: "/docs/ws/events" },
    ],
  },
    {
      title: "Cookbooks",
      items: [
        { title: "Start Transcription", href: "/docs/cookbook/start-transcription" },
        { title: "Track Meeting Status", href: "/docs/cookbook/track-meeting-status" },
        { title: "Get Transcripts", href: "/docs/cookbook/get-transcripts" },
        { title: "Live Transcripts", href: "/docs/cookbook/live-transcripts" },
        { title: "Share Transcript URL", href: "/docs/cookbook/share-transcript-url" },
        { title: "Rename Meeting", href: "/docs/cookbook/rename-meeting" },
        { title: "Get Status History", href: "/docs/cookbook/get-status-history" },
        { title: "Stop Bot", href: "/docs/cookbook/stop-bot" },
      ],
    },
  {
    title: "Admin API",
    items: [
      { title: "Users", href: "/docs/admin/users" },
      { title: "Tokens", href: "/docs/admin/tokens" },
    ],
  },
];

export default function DocsLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const pathname = usePathname();

  return (
    <div className="flex min-h-screen bg-background">
      {/* Sidebar */}
      <aside className="hidden lg:block w-64 border-r bg-background shrink-0">
        <div className="sticky top-0 h-screen overflow-hidden flex flex-col">
          <div className="p-6 border-b">
            <Link href="/docs" className="flex items-center gap-2 font-semibold hover:text-primary transition-colors">
              <BookOpen className="h-5 w-5" />
              <span>API Docs</span>
            </Link>
          </div>
          <ScrollArea className="flex-1">
            <nav className="p-4 space-y-6">
              {docsNav.map((section) => (
                <div key={section.title}>
                  <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">
                    {section.title}
                  </h3>
                  <ul className="space-y-1">
                    {section.items.map((item) => {
                      const isActive = pathname === item.href;
                      return (
                        <li key={item.href}>
                          <Link
                            href={item.href}
                            className={cn(
                              "flex items-center gap-2 px-3 py-2 text-sm rounded-md transition-colors",
                              isActive
                                ? "bg-primary text-primary-foreground"
                                : "hover:bg-accent hover:text-accent-foreground"
                            )}
                          >
                            {item.title}
                          </Link>
                        </li>
                      );
                    })}
                  </ul>
                </div>
              ))}
            </nav>
          </ScrollArea>
        </div>
      </aside>

      {/* Main Content */}
      <main className="flex-1">
        <div className="max-w-4xl mx-auto px-6 py-12">
          {children}
        </div>
      </main>
    </div>
  );
}

