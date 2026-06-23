"use client";

import { useState } from "react";
import { usePathname } from "next/navigation";
import { Header } from "./header";
import { Sidebar } from "./sidebar";

// Routes that don't need the full app layout
const publicRoutes = ["/login", "/auth", "/docs"];

interface AppLayoutProps {
  children: React.ReactNode;
}

export function AppLayout({ children }: AppLayoutProps) {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const pathname = usePathname();

  // Check if current route is public (shouldn't have sidebar/header)
  const isPublicRoute = publicRoutes.some((route) => pathname.startsWith(route));

  // For public routes, just render children without the app shell
  if (isPublicRoute) {
    return <>{children}</>;
  }

  return (
    <div className="h-screen flex flex-col bg-background overflow-hidden">
      <Header onMenuClick={() => setSidebarOpen(true)} />
      <div className="flex flex-1 overflow-hidden">
        <Sidebar isOpen={sidebarOpen} onClose={() => setSidebarOpen(false)} />
        <main className="flex-1 overflow-auto p-4 md:p-6">{children}</main>
      </div>
    </div>
  );
}
