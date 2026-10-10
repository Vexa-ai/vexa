import type { Metadata } from "next";
import "./globals.css";
import { Analytics } from "./AnalyticsScript";
// Geist Sans and Geist Mono (SIL OFL 1.1, served unmodified as web fonts; logged as a Category B
// exception in license-exceptions.json). Each sets a CSS variable that tokens.css reads.
import { GeistSans } from "geist/font/sans";
import { GeistMono } from "geist/font/mono";

export const metadata: Metadata = {
  title: "Vexa Terminal",
  description:
    "AI-first knowledge-worker terminal — Claude Code × Outlook on Vexa's meeting-bot + agentic-runtime backend.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${GeistSans.variable} ${GeistMono.variable}`}>
      <head>
        {/* apply the saved theme before first paint so day mode doesn't flash dark on reload */}
        <script dangerouslySetInnerHTML={{ __html: `try{if(localStorage.getItem('vexa.terminal.theme')==='light')document.documentElement.setAttribute('data-theme','light')}catch(e){}` }} />
      </head>
      <body>
        {children}
        <Analytics />
      </body>
    </html>
  );
}
