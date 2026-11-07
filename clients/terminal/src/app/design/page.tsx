/** /design — the terminal's design catalogue (terminal design guidelines §9).
 *
 *  OFF IN PRODUCTION unless the operator sets `VEXA_TERMINAL_DESIGN_CATALOGUE=1` (dev and staging
 *  set it; production leaves it unset): the route answers 404. `noindex, nofollow`, no analytics
 *  (AnalyticsScript skips `/design`), no sign-in — because it holds no user data at all: every
 *  entry renders FIXTURES, and the route imports nothing but the ui-kit front door, the tokens and
 *  its own files (gate G11, `designCatalogue.test.ts`). The `/mdx-demo` route is the precedent. */
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { Catalogue } from "./Catalogue";
import "./catalogue.css";
import { catalogueEnabled } from "./gate";

export const dynamic = "force-dynamic";   // the gate reads the environment per request, not at build
export const metadata: Metadata = {
  title: "Design catalogue · Vexa Terminal",
  robots: { index: false, follow: false },
};

export default function DesignPage() {
  if (!catalogueEnabled()) notFound();
  return <Catalogue />;
}
