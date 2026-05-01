"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { cn } from "@/lib/utils";
import { getDocsUrl, getWebappUrl } from "@/lib/docs/webapp-url";
import {
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
  Webhook,
  User,
  Bug,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useJoinModalStore } from "@/stores/join-modal-store";
import { useAdminAuthStore } from "@/stores/admin-auth-store";
import { AdminAuthModal } from "@/components/admin/admin-auth-modal";
import { useRuntimeConfig } from "@/hooks/use-runtime-config";
import { VersionChip } from "@/components/version-chip";
import { withBasePath } from "@/lib/base-path";

interface SidebarProps {
  isOpen?: boolean;
  onClose?: () => void;
}

const navigation = [
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

// IS_HOSTED is determined at runtime via /api/config, not build time

function BillingStatus() {
  const [status, setStatus] = useState<{
    subscription_status: string | null;
    subscription_tier: string | null;
    subscription_trial_end: string | null;
  } | null>(null);

  useEffect(() => {
    fetch(withBasePath("/api/billing/status"))
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
  const { config } = useRuntimeConfig();
  const isHosted = config?.hostedMode ?? false;

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

              {/* Below the line: integrations & settings */}
              <div className="mt-4 pt-4 border-t space-y-1">
                {/* Webhooks */}
                <Link
                  href="/webhooks"
                  onClick={onClose}
                  className={cn(
                    "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
                    pathname.startsWith("/webhooks")
                      ? "bg-primary text-primary-foreground"
                      : "text-muted-foreground hover:bg-accent hover:text-accent-foreground"
                  )}
                >
                  <Webhook className="h-5 w-5" />
                  Webhooks
                </Link>
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
          <div className="border-t border-border p-4 shrink-0 space-y-2">
            {isHosted && (
              <>
                <BillingStatus />
                <a
                  href={`${config?.webappUrl || "https://vexa.ai"}/account`}
                  onClick={onClose}
                  className="flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium transition-colors text-muted-foreground hover:bg-accent hover:text-accent-foreground"
                >
                  <CreditCard className="h-4 w-4" />
                  Account & Billing
                </a>
              </>
            )}
            <a
              href={getDocsUrl("/")}
              target="_blank"
              rel="noopener noreferrer"
              onClick={onClose}
              className="flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium transition-colors text-muted-foreground hover:bg-accent hover:text-accent-foreground"
            >
              <BookOpen className="h-4 w-4" />
              API Docs
            </a>
            <a
              href="https://github.com/Vexa-ai/vexa/issues/new?labels=bug,hosted&title=[Hosted]%20&body=%23%23%20Environment%0AHosted%20service%20(dashboard.vexa.ai)%0A%0A%23%23%20Description%0A%0A%23%23%20Steps%20to%20reproduce%0A1.%20%0A%0A%23%23%20Expected%20behavior%0A%0A%23%23%20Actual%20behavior%0A"
              target="_blank"
              rel="noopener noreferrer"
              onClick={onClose}
              className="flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium transition-colors text-muted-foreground hover:bg-accent hover:text-accent-foreground"
            >
              <Bug className="h-4 w-4" />
              Report a Bug
            </a>

            <div className="px-3">
              <div className="flex items-center gap-1.5">
                <span className="text-[11px] text-muted-foreground">vexa</span>
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
