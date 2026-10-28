/** /debug/readme — the README lab (0.13.4 S7): the per-viewer README, references and paste, Share,
 *  pins and the message sheet on synthetic fixtures, with their routes stubbed in the browser.
 *  NEVER SERVED IN PRODUCTION: a production build answers 404 (`labEnabled`). No sign-in, no user data. */
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { ReadmeLab } from "./ReadmeLab";
import { labEnabled } from "./gate";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "README lab · Vexa Terminal", robots: { index: false, follow: false } };

export default function ReadmeLabPage() {
  if (!labEnabled()) notFound();
  return <ReadmeLab />;
}
