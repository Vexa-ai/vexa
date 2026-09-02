"use client";
/** `/w/<workspace-id>/<path>` — the canonical document route.
 *
 *  Renders the SAME shell `/` does (exactly as `/meetings/<id>` does), then asks the server what
 *  the id means FOR THIS READER and opens the file in the panel through the shell's own
 *  open-entity event. Going through that event rather than through a prop is what keeps this route
 *  to one file: the shell already knows how to put a `{path, slug}` in the panel, and a second way
 *  in would be a second thing to keep in step.
 *
 *  Access is the server's answer, never the URL's. A link handed to somebody who is not in the
 *  workspace resolves to `not-yours`, and the honest response to that is to open nothing and leave
 *  the terminal as it is — *"if a workspace is not available, it's okay — by design"*.
 */
import { useEffect } from "react";
import { App } from "../../App";
import { workspaceRouteFromPath } from "../../workspaceRoute";
import { OPEN_ENTITY_EVENT } from "../../../canvas/actions";

export default function WorkspaceRoutePage() {
  useEffect(() => {
    const route = workspaceRouteFromPath(window.location.pathname);
    if (!route) return;
    let on = true;
    void (async () => {
      const api = await import("../../../surfaces/workspaceApi").catch(() => null);
      const rec = api ? await api.readWorkspaceById(route.workspace).catch(() => null) : null;
      if (!on || !rec || rec.access !== "readable") return;
      // The shell mounts its listener on its own first effect; dispatching on the next tick is
      // enough ordering, and a missed dispatch costs a panel that stayed where it was.
      setTimeout(() => {
        window.dispatchEvent(new CustomEvent(OPEN_ENTITY_EVENT, {
          detail: { path: route.path || "README.md", slug: rec.slug ?? undefined },
        }));
      }, 0);
    })();
    return () => { on = false; };
  }, []);
  return <App />;
}
