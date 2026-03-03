"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, useEffect } from "react";
import { Settings, Menu, LogOut, User, BookOpen, Code, FileCode, Code2, ExternalLink } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Logo } from "@/components/ui/logo";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { Separator } from "@/components/ui/separator";
import { ThemeToggle } from "@/components/theme-toggle";
import { useAuthStore } from "@/stores/auth-store";
import { cn } from "@/lib/utils";
import { getDocsUrl } from "@/lib/docs/webapp-url";

interface HeaderProps {
  onMenuClick?: () => void;
}

export function Header({ onMenuClick }: HeaderProps) {
  const router = useRouter();
  const { user, logout } = useAuthStore();
  const [docsModeEnabled, setDocsModeEnabled] = useState(false);

  useEffect(() => {
    // Read from localStorage on mount
    try {
      const stored = localStorage.getItem("vexa-docs-mode");
      if (stored) {
        const parsed = JSON.parse(stored);
        setDocsModeEnabled(parsed?.state?.enabled ?? false);
      }
    } catch {
      // Ignore errors
    }

    // Listen for changes
    const handleStorageChange = () => {
      try {
        const stored = localStorage.getItem("vexa-docs-mode");
        if (stored) {
          const parsed = JSON.parse(stored);
          setDocsModeEnabled(parsed?.state?.enabled ?? false);
        }
      } catch {
        // Ignore errors
      }
    };

    window.addEventListener("storage", handleStorageChange);
    return () => window.removeEventListener("storage", handleStorageChange);
  }, []);

  const toggleDocsMode = () => {
    const newValue = !docsModeEnabled;
    setDocsModeEnabled(newValue);
    try {
      const stored = localStorage.getItem("vexa-docs-mode");
      const existing = stored ? JSON.parse(stored) : {};
      localStorage.setItem(
        "vexa-docs-mode",
        JSON.stringify({
          ...existing,
          state: { enabled: newValue },
        })
      );
      // Trigger custom event for same-tab updates
      window.dispatchEvent(new CustomEvent("vexa-docs-mode-change"));
    } catch {
      // Ignore errors
    }
  };

  const handleLogout = () => {
    logout();
    router.push("/login");
  };

  return (
    <header className="shrink-0 z-50 w-full border-b border-border/70 bg-card/80 backdrop-blur-md">
      <div className="flex h-14 items-center px-4 md:px-6">
        {/* Mobile menu button */}
        <Button
          variant="ghost"
          size="icon"
          className="mr-2 md:hidden"
          onClick={onMenuClick}
        >
          <Menu className="h-5 w-5" />
          <span className="sr-only">Toggle menu</span>
        </Button>

        {/* Logo */}
        <Link href="/" className="flex items-center gap-2 group">
          <Logo size="md" showText={false} className="group-hover:scale-105 transition-transform" />
          <span className="hidden sm:inline-block text-[15px] font-semibold tracking-[-0.01em] text-foreground">vexa</span>
        </Link>

        {/* Spacer */}
        <div className="flex-1" />

        {/* Actions */}
        <div className="flex items-center gap-2">
          <Popover>
            <PopoverTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className={cn(
                  "transition-colors",
                  docsModeEnabled 
                    ? "bg-primary/10 text-primary hover:bg-primary/20" 
                    : "text-muted-foreground hover:text-foreground"
                )}
              >
                <Code2 className="h-5 w-5" />
                <span className="sr-only">API Docs Mode</span>
              </Button>
            </PopoverTrigger>
            <PopoverContent side="bottom" align="end" className="w-80 p-4">
              <div className="space-y-4">
                <div className="space-y-2">
                  <div className="flex items-center gap-2">
                    <div className={cn(
                      "rounded-md p-1.5",
                      docsModeEnabled ? "bg-primary/10" : "bg-muted"
                    )}>
                      <Code className={cn(
                        "h-4 w-4",
                        docsModeEnabled ? "text-primary" : "text-muted-foreground"
                      )} />
                    </div>
                    <div className="flex-1">
                      <h4 className="font-semibold text-sm text-foreground">API Docs Mode</h4>
                      <p className="text-xs text-muted-foreground mt-0.5">
                        {docsModeEnabled ? "Enabled" : "Disabled"}
                      </p>
                    </div>
                  </div>
                  <p className="text-xs text-muted-foreground leading-relaxed">
                    When enabled, contextual API documentation badges appear throughout the UI, showing you how to perform the same actions using the Vexa API.
                  </p>
                </div>
                
                <div className="space-y-2.5 rounded-lg border bg-muted/50 p-3">
                  <p className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Visual indicator:</p>
                  <div className="flex items-center gap-2">
                    <div className="inline-flex items-center justify-center px-2 py-1 rounded-md bg-muted border border-border shrink-0">
                      <Code2 className="h-3.5 w-3.5 text-muted-foreground" />
                    </div>
                    <p className="text-xs text-muted-foreground flex-1">
                      Look for these badges next to UI actions
                    </p>
                  </div>
                </div>

                <div className="flex items-center gap-2 pt-2 border-t">
                  <Button
                    variant={docsModeEnabled ? "outline" : "default"}
                    size="sm"
                    onClick={toggleDocsMode}
                    className="flex-1 text-xs"
                  >
                    {docsModeEnabled ? "Disable" : "Enable"} Mode
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    className="text-xs"
                    asChild
                  >
                    <a href={getDocsUrl()} target="_blank" rel="noopener noreferrer">
                      <BookOpen className="h-3.5 w-3.5 mr-1.5" />
                      Docs
                    </a>
                  </Button>
                </div>
              </div>
            </PopoverContent>
          </Popover>

          <Separator orientation="vertical" className="mx-1 h-6" />

          <Tooltip>
            <TooltipTrigger asChild>
              <Button variant="ghost" size="icon" asChild className="text-muted-foreground">
                <Link href="/docs">
                  <BookOpen className="h-5 w-5" />
                  <span className="sr-only">Full Documentation</span>
                </Link>
              </Button>
            </TooltipTrigger>
            <TooltipContent>
              <p>Full API Documentation</p>
            </TooltipContent>
          </Tooltip>

          <ThemeToggle />

          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon">
                <User className="h-5 w-5" />
                <span className="sr-only">User menu</span>
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-56">
              {user && (
                <>
                  <div className="px-2 py-1.5 text-sm">
                    <p className="font-medium">{user.name || user.email}</p>
                    <p className="text-xs text-muted-foreground">{user.email}</p>
                  </div>
                  <DropdownMenuSeparator />
                </>
              )}
              <DropdownMenuItem asChild>
                <Link href="/settings">
                  <Settings className="mr-2 h-4 w-4" />
                  Settings
                </Link>
              </DropdownMenuItem>
              {process.env.NEXT_PUBLIC_HOSTED_MODE === "true" && (
                <DropdownMenuItem asChild>
                  <a href={`${process.env.NEXT_PUBLIC_WEBAPP_URL || "https://vexa.ai"}/account`}>
                    <ExternalLink className="mr-2 h-4 w-4" />
                    Account
                  </a>
                </DropdownMenuItem>
              )}
              <DropdownMenuSeparator />
              <DropdownMenuItem onClick={handleLogout} className="text-destructive focus:text-destructive">
                <LogOut className="mr-2 h-4 w-4" />
                Logout
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>
    </header>
  );
}
