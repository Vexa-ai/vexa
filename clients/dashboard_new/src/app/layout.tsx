import type { ReactNode } from "react";
import "./globals.css";
import { Providers } from "./providers";

export const metadata = {
  title: "Vexa Dashboard",
  description: "Live meetings, transcripts, and recordings — modular dashboard.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <Providers>
          <nav className="app-nav">
            <span className="brand">VEXA</span>
            <a href="/meetings">Meetings</a>
            <a href="/join">Start a bot</a>
          </nav>
          <main className="app-shell">{children}</main>
        </Providers>
      </body>
    </html>
  );
}
