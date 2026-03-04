"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { cn } from "@/lib/utils";
import { getDocsUrl, getWebappUrl } from "@/lib/docs/webapp-url";
import {
  LayoutDashboard,
  Video,
  Plus,
  Settings,
  X,
  Users,
  Shield,
  LogOut,
  Lock,
  Bot,
  BookOpen,
  Zap,
  CreditCard,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useJoinModalStore } from "@/stores/join-modal-store";
import { useAdminAuthStore } from "@/stores/admin-auth-store";
import { AdminAuthModal } from "@/components/admin/admin-auth-modal";

interface SidebarProps {
  isOpen?: boolean;
  onClose?: () => void;
}

const navigation = [
  { name: "Dashboard", href: "/", icon: LayoutDashboard },
  { name: "Meetings", href: "/meetings", icon: Video },
  ...(process.env.NEXT_PUBLIC_TRACKER_ENABLED === "true"
    ? [{ name: "Tracker", href: "/tracker", icon: Zap }]
    : []),
];

const adminNavigation = [
  { name: "Users", href: "/admin/users", icon: Users },
  { name: "Bots", href: "/admin/bots", icon: Bot },
  { name: "Settings", href: "/settings", icon: Settings },
];

const IS_HOSTED = process.env.NEXT_PUBLIC_HOSTED_MODE === "true";

function BillingStatus() {
  const [status, setStatus] = useState<{
    subscription_status: string | null;
    subscription_tier: string | null;
    subscription_trial_end: string | null;
  } | null>(null);

  useEffect(() => {
    fetch("/api/billing/status")
      .then((r) => r.json())
      .then(setStatus)
      .catch(() => {});
  }, []);

  if (!status || !status.subscription_status) return null;

  const { subscription_status, subscription_tier, subscription_trial_end } =
    status;

  if (subscription_status === "trialing" && subscription_trial_end) {
    const daysLeft = Math.max(
      0,
      Math.ceil(
        (new Date(subscription_trial_end).getTime() - Date.now()) /
          (1000 * 60 * 60 * 24)
      )
    );
    return (
      <div className="px-3 py-1.5">
        <span className="text-xs font-medium text-amber-500">
          Trial: {daysLeft} day{daysLeft !== 1 ? "s" : ""} left
        </span>
      </div>
    );
  }

  if (
    subscription_status === "canceled" ||
    subscription_status === "expired"
  ) {
    return (
      <div className="px-3 py-1.5 flex items-center justify-between">
        <span className="text-xs font-medium text-red-500">Plan expired</span>
        <a
          href={`${getWebappUrl()}/pricing`}
          target="_blank"
          rel="noopener noreferrer"
          className="text-xs font-medium text-primary hover:underline"
        >
          Subscribe
        </a>
      </div>
    );
  }

  if (subscription_status === "active") {
    const label = subscription_tier
      ? subscription_tier.charAt(0).toUpperCase() + subscription_tier.slice(1)
      : "Active";
    return (
      <div className="px-3 py-1.5">
        <span className="text-xs font-medium text-muted-foreground">
          {label} plan
        </span>
      </div>
    );
  }

  return null;
}

export function Sidebar({ isOpen, onClose }: SidebarProps) {
  const pathname = usePathname();
  const router = useRouter();
  const openJoinModal = useJoinModalStore((state) => state.openModal);
  const { isAdminAuthenticated, logout: adminLogout } = useAdminAuthStore();
  const [showAdminAuthModal, setShowAdminAuthModal] = useState(false);

  const handleJoinClick = () => {
    openJoinModal();
    onClose?.();
  };

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
              {/* Join Meeting button */}
              <button
                onClick={handleJoinClick}
                className="flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors text-muted-foreground hover:bg-accent hover:text-accent-foreground"
              >
                <Plus className="h-5 w-5" />
                Join Meeting
              </button>

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
          <div className="border-t border-border p-4 shrink-0 space-y-2">
            {IS_HOSTED && (
              <>
                <BillingStatus />
                <a
                  href={`${getWebappUrl()}/account`}
                  target="_blank"
                  rel="noopener noreferrer"
                  onClick={onClose}
                  className="flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium transition-colors text-muted-foreground hover:bg-accent hover:text-accent-foreground"
                >
                  <CreditCard className="h-4 w-4" />
                  Account & Billing
                </a>
              </>
            )}
            <a
              href={getDocsUrl()}
              target="_blank"
              rel="noopener noreferrer"
              onClick={onClose}
              className="flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium transition-colors text-muted-foreground hover:bg-accent hover:text-accent-foreground"
            >
              <BookOpen className="h-4 w-4" />
              API Docs
            </a>
            <div className="px-3">
              <p className="text-[11px] text-muted-foreground">
                vexa v1.0
              </p>
              <p className="text-[11px] text-muted-foreground">
                Open Source · API-first
              </p>
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
