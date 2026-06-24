import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Vexa Terminal",
  description:
    "AI-first knowledge-worker terminal — Claude Code × Outlook on Vexa's meeting-bot + agentic-runtime backend.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
