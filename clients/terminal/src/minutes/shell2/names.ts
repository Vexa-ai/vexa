/** Workspace names for the centre lists, read from the desktop the home already loads — no read of
 *  its own. The desk is "My desk" (UI 7y). An unknown slug shows as itself. */
import { useDesktopState } from "../desktop/desktopApi";
import { workspaceDisplayName } from "../vocabulary";

export function useWorkspaceNames(): (slug: string | null | undefined) => string {
  const { desktop } = useDesktopState();
  const items = desktop?.sections.find((s) => s.id === "workspaces")?.items ?? [];
  const map = new Map<string, string>();
  for (const i of items) if (i.kind === "workspace") map.set((i as { slug?: string }).slug || "personal", (i as { name?: string }).name ?? "");
  return (slug) => workspaceDisplayName(slug || "personal", map.get(slug || "personal") || null);
}
