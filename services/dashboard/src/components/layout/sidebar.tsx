"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { cn } from "@/lib/utils";
import {
  Video,
  Settings,
  X,
  Users,
  Shield,
  LogOut,
  Lock,
  Bot,
  User,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useAdminAuthStore } from "@/stores/admin-auth-store";
import { AdminAuthModal } from "@/components/admin/admin-auth-modal";
import { VersionChip } from "@/components/version-chip";

interface SidebarProps {
  isOpen?: boolean;
  onClose?: () => void;
}

const navigation = [
  { name: "Meetings", href: "/meetings", icon: Video },
];

const adminNavigation = [
  { name: "Users", href: "/admin/users", icon: Users },
  { name: "Bots", href: "/admin/bots", icon: Bot },
  { name: "Settings", href: "/settings", icon: Settings },
];

export function Sidebar({ isOpen, onClose }: SidebarProps) {
  const pathname = usePathname();
  const router = useRouter();
  const { isAdminAuthenticated, logout: adminLogout } = useAdminAuthStore();
  const [showAdminAuthModal, setShowAdminAuthModal] = useState(false);

  const handleAdminClick = (href: string) => {
    if (isAdminAuthenticated) {
      router.push(href);
      onClose?.();
    } else {
      setShowAdminAuthModal(true);
    }
  };

  const handleAdminAuthSuccess = () => {
    // Redirect to admin after successful auth
    router.push("/admin/users");
    onClose?.();
  };

  const handleAdminLogout = () => {
    adminLogout();
  };

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

              {/* Below the line: profile & settings */}
              <div className="mt-4 pt-4 border-t space-y-1">
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
              </div>

              {/* Admin Section */}
              <div className="mt-6 pt-4 border-t">
                <div className="flex items-center justify-between px-3 mb-2">
                  <div className="flex items-center gap-2">
                    <Shield className="h-4 w-4 text-muted-foreground" />
                    <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                      Admin
                    </span>
                  </div>
                  {isAdminAuthenticated && (
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-6 w-6"
                      onClick={handleAdminLogout}
                      title="Logout from admin"
                    >
                      <LogOut className="h-3 w-3 text-muted-foreground" />
                    </Button>
                  )}
                </div>

                {isAdminAuthenticated ? (
                  // Show admin navigation when authenticated
                  adminNavigation.map((item) => {
                    const isActive = pathname.startsWith(item.href);

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
                  })
                ) : (
                  // Show login prompt when not authenticated
                  <button
                    onClick={() => setShowAdminAuthModal(true)}
                    className="flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors text-muted-foreground hover:bg-accent hover:text-accent-foreground"
                  >
                    <Lock className="h-5 w-5" />
                    <span>Unlock Admin</span>
                  </button>
                )}
              </div>
            </nav>
          </ScrollArea>

          {/* Footer */}
          <div className="border-t border-border p-4 shrink-0">
            <div className="px-3">
              <div className="flex items-center gap-1.5">
                <span className="text-[11px] text-muted-foreground">Grainbox</span>
                <VersionChip variant="minimal" look="pill" />
              </div>
            </div>
          </div>
        </div>
      </aside>

      {/* Admin Auth Modal */}
      <AdminAuthModal
        open={showAdminAuthModal}
        onOpenChange={setShowAdminAuthModal}
        onSuccess={handleAdminAuthSuccess}
      />
    </>
  );
}
