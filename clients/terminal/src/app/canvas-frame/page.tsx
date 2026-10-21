/** `/canvas-frame` — the document a Meeting Canvas view runs in. Served with its own policy
 *  (`canvasFrameContentSecurityPolicy`, middleware.ts): `sandbox allow-scripts`, an opaque origin,
 *  no connections, framed by this terminal's pages only. See `canvas/frame/protocol.ts`. */
import { CanvasFrame } from "../../canvas/frame/CanvasFrame";

export const dynamic = "force-dynamic";

export default function CanvasFramePage() {
  return <CanvasFrame />;
}
