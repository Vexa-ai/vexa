import type { ReactNode } from "react";
import type { Metadata } from "next";
import { GeistSans } from "geist/font/sans";
import { GeistMono } from "geist/font/mono";
import "./globals.css";
import { Providers } from "./providers";
import { ThemeProvider } from "@/components/theme-provider";
import { AppLayout } from "@/components/layout/app-layout";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Toaster } from "@/components/ui/sonner";

export const metadata: Metadata = {
  title: "Vexa Dashboard",
  description: "Live meetings, transcripts, and recordings — modular dashboard.",
  icons: {
    icon: [{ url: "/icons/vexadark.svg", type: "image/svg+xml" }],
    apple: [{ url: "/icons/vexadark.svg", type: "image/svg+xml" }],
  },
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body
        className={`${GeistSans.variable} ${GeistMono.variable} antialiased`}
      >
        <ThemeProvider
          attribute="class"
          defaultTheme="dark"
          enableSystem
          disableTransitionOnChange
        >
          <Providers>
            <TooltipProvider delayDuration={0}>
              <AppLayout>{children}</AppLayout>
            </TooltipProvider>
          </Providers>
          <Toaster position="bottom-right" />
        </ThemeProvider>
      </body>
    </html>
  );
}
