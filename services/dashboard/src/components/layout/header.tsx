"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { Settings, Menu, LogOut, User, ExternalLink } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Logo } from "@/components/ui/logo";
import { VersionChip } from "@/components/version-chip";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { ThemeToggle } from "@/components/theme-toggle";
import { SearchBar } from "@/components/search-bar";
import { useAuthStore } from "@/stores/auth-store";
import { useRuntimeConfig } from "@/hooks/use-runtime-config";

interface HeaderProps {
  onMenuClick?: () => void;
}

export function Header({ onMenuClick }: HeaderProps) {
  const router = useRouter();
  const { user, logout } = useAuthStore();
  const { config } = useRuntimeConfig();

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

        {/* Logo + version chip */}
        <div className="flex items-center gap-2.5">
          <Link href="/" className="flex items-center gap-2 group">
            <Logo size="md" showText={false} className="group-hover:scale-105 transition-transform" />
            <span className="hidden sm:inline-block text-[15px] font-semibold tracking-[-0.01em] text-foreground">Grainbox</span>
          </Link>
          <VersionChip className="hidden sm:inline-flex" />
        </div>

        {/* Spacer */}
        <div className="flex-1" />

        {/* Global Search — prominent, Grain-style */}
        <div className="hidden md:flex w-80 lg:w-96 mx-4">
          <SearchBar />
        </div>
        <div className="flex-1" />

        {/* Actions */}
        <div className="flex items-center gap-2">
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
                <Link href="/profile">
                  <Settings className="mr-2 h-4 w-4" />
                  Profile & API Key
                </Link>
              </DropdownMenuItem>
              {config?.hostedMode && (
                <DropdownMenuItem asChild>
                  <a href={`${config?.webappUrl || "https://vexa.ai"}/account`}>
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
