/** `/canvas-frame` — the document a Meeting Canvas view runs in. Served with its own policy
 *  (`canvasFrameContentSecurityPolicy`, middleware.ts): `sandbox allow-scripts`, an opaque origin,
 *  no connections, framed by this terminal's pages only — from the canvas origin when the deployment
 *  names one (`TERMINAL_CANVAS_ORIGIN`, ../canvasOrigin.mjs). See `canvas/frame/protocol.ts`. */
import { CanvasFrame } from "../../canvas/frame/CanvasFrame";
import { canvasOrigin, terminalOrigins } from "../canvasOrigin.mjs";

export const dynamic = "force-dynamic";

export default function CanvasFramePage() {
  // On its own origin the frame talks to the terminal's public origins only; on the terminal's
  // origin, to the origin it was served from (canvas/frame/protocol.ts `parentOriginsFor`).
  const parents = canvasOrigin(process.env) ? terminalOrigins(process.env) : [];
  return <CanvasFrame parentOrigins={parents} />;
}
