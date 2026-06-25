/** Engine commands + default keybindings — registered by the workbench at boot (not a surface).
 *  Everything the palette + keyboard drive (palette, list switch, new session, toggle/reset panes) is a
 *  command in the one registry; keybindings point at command ids. */
import { CommandServiceId, KeybindingServiceId, type ServiceContainer } from "../platform";
import { registry } from "../contributions";
import { LayoutServiceId } from "./layout";
import { PaletteServiceId } from "./palette";

export function registerEngineCommands(container: ServiceContainer): void {
  const cmd = container.get(CommandServiceId);

  cmd.register({ id: "palette.toggle", title: "Command Palette", run: ({ container: c }) => c.get(PaletteServiceId).toggle() });
  cmd.register({ id: "workbench.toggleLeft", title: "Toggle Left Sidebar", run: ({ container: c }) => c.get(LayoutServiceId).toggleLeft() });
  cmd.register({ id: "workbench.toggleRight", title: "Toggle Right Sidebar", run: ({ container: c }) => c.get(LayoutServiceId).toggleRight() });
  cmd.register({ id: "workbench.resetLayout", title: "Reset Layout", run: ({ container: c }) => c.get(LayoutServiceId).resetLayout() });
  cmd.register({ id: "chat.new", title: "New Session", run: ({ container: c }) => c.get(LayoutServiceId).openTab({ id: `chat:${Date.now().toString(36)}`, title: "New chat", kind: "chat", params: { subject: "u_live", session: null }, context: null }) });

  // one "show list" command per registered left list (generated from the registry).
  for (const l of registry.lists()) {
    cmd.register({ id: `list.${l.id}`, title: `Show: ${l.label}`, run: ({ container: c }) => c.get(LayoutServiceId).setActiveList(l.id) });
  }

  const kb = container.get(KeybindingServiceId);
  kb.register({ key: "$mod+k", command: "palette.toggle" });
  kb.register({ key: "$mod+p", command: "palette.toggle" });
  kb.register({ key: "$mod+b", command: "workbench.toggleLeft" });
  kb.register({ key: "$mod+j", command: "workbench.toggleRight" });
}
