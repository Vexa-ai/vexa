import type { Metadata } from "next";
import "./globals.css";
import { Analytics } from "./AnalyticsScript";
import { BRAND } from "./brand";
import { THEME_KEY } from "./themeKey";

export const metadata: Metadata = {
  title: `${BRAND.name} Terminal`,
  description:
    "AI-first knowledge-worker terminal — the workbench over your meeting-bot + agentic-runtime backend.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        {/* apply the saved theme before first paint so day mode doesn't flash dark on reload */}
        <script dangerouslySetInnerHTML={{ __html: `try{if(localStorage.getItem(${JSON.stringify(THEME_KEY)})==='light')document.documentElement.setAttribute('data-theme','light')}catch(e){}` }} />
        {/* One brand override, ahead of every stylesheet rule that reads it. Unset ⇒ no rule at
            all, so globals.css keeps its own default and an unbranded build is unchanged. */}
        {BRAND.accent || BRAND.onAccent || BRAND.font || BRAND.accentLight
          ? <style dangerouslySetInnerHTML={{ __html: [
              `:root{${[
                BRAND.accent && `--brand-accent:${BRAND.accent}`,
                BRAND.onAccent && `--brand-on-accent:${BRAND.onAccent}`,
                BRAND.font && `--brand-sans:${BRAND.font}`,
              ].filter(Boolean).join(";")}}`,
              // The light theme takes its own accent when the brand supplies one: a colour chosen
              // to read on a dark UI is usually too pale on white. Same selector globals.css uses.
              BRAND.accentLight && `:root[data-theme="light"]{--brand-accent:${BRAND.accentLight}}`,
            ].filter(Boolean).join("") }} />
          : null}
      </head>
      <body>
        {children}
        <Analytics />
      </body>
    </html>
  );
}
