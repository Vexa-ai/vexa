"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import { Video, Plus, Settings, X, User } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";

interface SidebarProps {
  isOpen?: boolean;
  onClose?: () => void;
}

const navigation = [
  { name: "Meetings", href: "/meetings", icon: Video },
  { name: "Join", href: "/join", icon: Plus },
];

export function Sidebar({ isOpen, onClose }: SidebarProps) {
  const pathname = usePathname();

  return (
    <>
      {/* Mobile overlay */}
      {isOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/50 md:hidden"
          onClick={onClose}
        />
      )}

      {/* Sidebar - fixed on mobile, relative on desktop */}
      <aside
        className={cn(
          // Mobile: fixed, full height, slides in
          "fixed inset-y-0 left-0 z-50 w-64 bg-card border-r border-border",
          "transform transition-transform duration-200 ease-in-out",
          // Desktop: relative, part of flex layout
          "md:relative md:z-0 md:translate-x-0 md:flex md:flex-col md:shrink-0",
          // Mobile visibility
          isOpen ? "translate-x-0" : "-translate-x-full md:translate-x-0"
        )}
      >
        <div className="flex h-full flex-col">
          {/* Mobile header */}
          <div className="flex h-14 items-center justify-between border-b px-4 md:hidden shrink-0">
            <span className="font-semibold">Menu</span>
            <Button variant="ghost" size="icon" onClick={onClose}>
              <X className="h-5 w-5" />
            </Button>
          </div>

          {/* Navigation - scrollable area */}
          <ScrollArea className="flex-1">
            <nav className="space-y-1 p-4">
              {navigation.map((item) => {
                const isActive =
                  item.href === "/"
                    ? pathname === "/"
                    : pathname.startsWith(item.href);

                return (
                  <Link
                    key={item.name}
                    href={item.href}
                    onClick={onClose}
                    className={cn(
                      "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
                      isActive
                        ? "bg-primary text-primary-foreground"
                        : "text-muted-foreground hover:bg-accent hover:text-accent-foreground"
                    )}
                  >
                    <item.icon className="h-5 w-5" />
                    {item.name}
                  </Link>
                );
              })}

              {/* Below the line: integrations & settings */}
              <div className="mt-4 pt-4 border-t space-y-1">
                {/* MCP Setup */}
                <Link
                  href="/mcp"
                  onClick={onClose}
                  className={cn(
                    "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
                    pathname.startsWith("/mcp")
                      ? "bg-primary text-primary-foreground"
                      : "text-muted-foreground hover:bg-accent hover:text-accent-foreground"
                  )}
                >
                  <span className="h-5 w-5 flex items-center justify-center">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src="/icons/icons8-mcp-96 (1).png"
                      alt="MCP"
                      width={20}
                      height={20}
                      className={cn(
                        "dark:invert opacity-70",
                        pathname.startsWith("/mcp") && "invert dark:invert-0 opacity-100"
                      )}
                    />
                  </span>
                  MCP Setup
                </Link>
                {/* Profile */}
                <Link
                  href="/profile"
                  onClick={onClose}
                  className={cn(
                    "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
                    pathname.startsWith("/profile")
                      ? "bg-primary text-primary-foreground"
                      : "text-muted-foreground hover:bg-accent hover:text-accent-foreground"
                  )}
                >
                  <User className="h-5 w-5" />
                  Profile
                </Link>
                {/* Settings */}
                <Link
                  href="/settings"
                  onClick={onClose}
                  className={cn(
                    "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
                    pathname.startsWith("/settings")
                      ? "bg-primary text-primary-foreground"
                      : "text-muted-foreground hover:bg-accent hover:text-accent-foreground"
                  )}
                >
                  <Settings className="h-5 w-5" />
                  Settings
                </Link>
              </div>
            </nav>
          </ScrollArea>
        </div>
      </aside>
    </>
  );
}
