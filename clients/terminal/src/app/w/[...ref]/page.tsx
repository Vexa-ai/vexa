"use client";
/** `/w/<workspace-id-or-slug>/<path>` — the canonical document route.
 *
 *  It renders the SAME shell `/` does (exactly as `/meetings/<id>` does) AND NOTHING ELSE. The URL
 *  is read by the shell itself, because the shell is the only thing that can honour it: opening the
 *  page it names is one move with choosing the chat it opens in and, when the link cannot open,
 *  saying so in the panel instead of the desk's README.
 *
 *  ⚠ IT USED TO DO THE WORK HERE, and that is what Vexa-ai/vexa#1643 is (in part). This page
 *  resolved the id itself and dispatched the shell's open-entity event on a `setTimeout(0)` — one
 *  more writer of the pages panel, racing the shell's own first layout, with no say over which chat
 *  the shell had opened underneath it and no way to render a refusal. The route's job is to render
 *  the shell; the shell's job is to read the address bar. Two writers on one surface do not error —
 *  they produce a plausible result and lose one writer's intent, which here was the whole link.
 *
 *  The parsing contract lives in `../../workspaceRoute.ts` (pure, unit-tested), the decision in
 *  `minutes/deepLink.ts` (pure, unit-tested), and the wiring in `minutes/MinutesShell.tsx`.
 */
import { App } from "../../App";

export default function WorkspaceRoutePage() {
  return <App />;
}
