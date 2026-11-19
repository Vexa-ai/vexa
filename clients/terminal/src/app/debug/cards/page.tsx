/** /debug/cards — the card lab (0.13.4 S5): the card components on synthetic fixtures with the card
 *  verbs stubbed in the browser. NEVER SERVED IN PRODUCTION: a production build answers 404
 *  (`labEnabled`). No sign-in, no user data, `noindex`. */
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { CardsLab } from "./CardsLab";
import { labEnabled } from "./gate";

export const dynamic = "force-dynamic";   // the gate reads the environment per request
export const metadata: Metadata = { title: "Card lab · Vexa Terminal", robots: { index: false, follow: false } };

export default function CardsLabPage() {
  if (!labEnabled()) notFound();
  return <CardsLab />;
}
