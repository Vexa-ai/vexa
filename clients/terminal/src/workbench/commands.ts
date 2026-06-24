/** Engine commands + default keybindings — registered by the workbench at boot (not a surface).
 *  Everything the palette and keyboard can drive (open the palette, switch surface, toggle/reset the
 *  layout) is a command in the one CommandRegistry; keybindings just point at command ids. */
import { CommandServiceId, KeybindingServiceId, type ServiceContainer } from "../platform";
import { registry } from "../contributions";
import { LayoutServiceId } from "./layout";
import { PaletteServiceId } from "./palette";

export function registerEngineCommands(container: ServiceContainer): void {
  const cmd = container.get(CommandServiceId);

  cmd.register({ id: "palette.toggle", title: "Command Palette", run: ({ container: c }) => c.get(PaletteServiceId).toggle() });
  cmd.register({ id: "workbench.toggleSidebar", title: "Toggle Sidebar", run: ({ container: c }) => c.get(LayoutServiceId).toggleSidebar() });
  cmd.register({ id: "workbench.resetLayout", title: "Reset Layout", run: ({ container: c }) => c.get(LayoutServiceId).resetLayout() });

  // one open-command per surface, generated from the registry (additive — no per-surface code here).
  for (const a of registry.activityItems()) {
    cmd.register({ id: `surface.open.${a.id}`, title: `Open: ${a.label}`, run: ({ container: c }) => c.get(LayoutServiceId).openSurface(a.id) });
  }

  // default keybindings ($mod = ⌘ on mac / Ctrl elsewhere). ⌘1..9 avoided — they collide with the
  // browser's tab switching; surface switching goes through the palette instead.
  const kb = container.get(KeybindingServiceId);
  kb.register({ key: "$mod+k", command: "palette.toggle" });
  kb.register({ key: "$mod+p", command: "palette.toggle" });
  kb.register({ key: "$mod+b", command: "workbench.toggleSidebar" });
}
