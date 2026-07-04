"use client";
/** Analytics — loads Google Analytics 4 (gtag.js) and reports SPA page views.
 *
 *  The measurement id is `NEXT_PUBLIC_GA_MEASUREMENT_ID`, inlined at BUILD time (Docker build ARG →
 *  next build). When it's unset the component renders nothing and the whole analytics layer stays a
 *  no-op — GA is strictly opt-in. Changing the id requires a rebuild (that's how NEXT_PUBLIC_* works).
 *
 *  The App Router doesn't trigger gtag's automatic page_view on client-side navigations, so we send the
 *  initial page_view via gtag config and every SUBSEQUENT one from a pathname effect (skipping the first
 *  run to avoid double-counting the load). */
import Script from "next/script";
import { useEffect, useRef } from "react";
import { usePathname } from "next/navigation";
import { gaPageview } from "./analytics";

const GA_ID = process.env.NEXT_PUBLIC_GA_MEASUREMENT_ID;

export function Analytics() {
  const pathname = usePathname();
  const firstRun = useRef(true);

  useEffect(() => {
    if (!GA_ID) return;
    if (firstRun.current) {            // the load's page_view is sent by gtag config below
      firstRun.current = false;
      return;
    }
    if (pathname) gaPageview(pathname);
  }, [pathname]);

  if (!GA_ID) return null;
  return (
    <>
      <Script src={`https://www.googletagmanager.com/gtag/js?id=${GA_ID}`} strategy="afterInteractive" />
      <Script id="ga-init" strategy="afterInteractive">
        {`window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}window.gtag=gtag;gtag('js',new Date());gtag('config','${GA_ID}');`}
      </Script>
    </>
  );
}
