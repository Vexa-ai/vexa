"use client";
/** CommandPalette — the ⌘K quick-input over the CommandRegistry + the registry surfaces (go-to).
 *  Headless cmdk does the filtering/keyboard; we own the overlay + styling. Select → execute/open +
 *  close. Esc / click-outside closes. */
import { type CSSProperties, type KeyboardEvent } from "react";
import { Command } from "cmdk";
import { useService, useStore, CommandServiceId } from "../platform";
import { PaletteServiceId } from "./palette";
import { LayoutServiceId } from "./layout";
import { registry } from "../contributions";

const overlay: CSSProperties = { position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)", display: "flex", alignItems: "flex-start", justifyContent: "center", paddingTop: "12vh", zIndex: 1000 };
const panel: CSSProperties = { width: 560, maxWidth: "92vw", background: "var(--panel)", border: "1px solid var(--line2)", borderRadius: 14, boxShadow: "0 20px 60px rgba(0,0,0,0.5)", overflow: "hidden" };

export function CommandPalette() {
  const palette = useService(PaletteServiceId);
  const { open } = useStore(palette.store);
  const commands = useService(CommandServiceId);
  const layout = useService(LayoutServiceId);
  if (!open) return null;

  const lists = registry.lists();
  const cmds = commands.all().filter((c) => !c.id.startsWith("list."));
  const onKeyDown = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopPropagation(); palette.close(); } };  // consume: close-topmost beats nav.back

  return (
    <div style={overlay} onClick={palette.close}>
      <div style={panel} onClick={(e) => e.stopPropagation()} onKeyDown={onKeyDown}>
        <Command label="Command palette">
          <Command.Input autoFocus placeholder="Search commands, or go to a surface…" />
          <Command.List>
            <Command.Empty>No matches.</Command.Empty>
            <Command.Group heading="Lists">
              {lists.map((l) => (
                <Command.Item key={l.id} value={`list ${l.label}`} onSelect={() => { layout.setActiveList(l.id); palette.close(); }}>
                  {l.label}
                </Command.Item>
              ))}
            </Command.Group>
            <Command.Group heading="Commands">
              {cmds.map((c) => (
                <Command.Item key={c.id} value={c.title + (c.skill ?? "")} onSelect={() => { void commands.execute(c.id); palette.close(); }}>
                  {c.title}
                  {c.skill && <span className="pal-skill">{c.skill}</span>}
                </Command.Item>
              ))}
            </Command.Group>
          </Command.List>
        </Command>
      </div>
    </div>
  );
}
