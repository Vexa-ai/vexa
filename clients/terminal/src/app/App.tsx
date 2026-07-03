"use client";
/** The composition root — builds the DI container, wires surface commands, renders the workbench.
 *  Importing `../surfaces` registers every surface as a load-time side effect (before this body runs). */
import { useEffect, useState } from "react";
import {
  createContainer, reg, ServicesProvider,
  CommandServiceId, createCommandService,
  ContextKeyServiceId, createContextKeyService,
  KeybindingServiceId, createKeybindingService,
} from "../platform";
import { LayoutServiceId, createLayoutService } from "../workbench/layout";
import { PaletteServiceId, createPaletteService } from "../workbench/palette";
import { registerEngineCommands } from "../workbench/commands";
import { Workbench } from "../workbench/Workbench";
import { registry } from "../contributions";
import { AuthGate } from "./AuthGate";
import { OnboardingGate } from "./OnboardingGate";
import { meetingsOnly } from "./mode";
import "../surfaces";

const container = createContainer([
  reg(ContextKeyServiceId, () => createContextKeyService()),
  reg(CommandServiceId, (c) => createCommandService(c)),
  // Meetings-only mode has no Sessions list — land on Meetings instead.
  reg(LayoutServiceId, () => createLayoutService(meetingsOnly() ? "meetings" : "sessions")),
  reg(PaletteServiceId, () => createPaletteService()),
  reg(KeybindingServiceId, (c) => createKeybindingService(c)),
]);
registry.commands().forEach((c) => container.get(CommandServiceId).register(c));
registerEngineCommands(container); // engine commands (palette/layout/open-surface) + default keybindings

export function App() {
  // The workbench is a client-only shell (localStorage-driven layout, dockview). Gate render until
  // mounted so the server HTML (which can't see localStorage/dockview) matches — no hydration mismatch.
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  if (!mounted) return <div style={{ height: "100vh", background: "var(--bg)" }} />;
  return (
    <AuthGate>
      <OnboardingGate>
        <ServicesProvider container={container}>
          <Workbench />
        </ServicesProvider>
      </OnboardingGate>
    </AuthGate>
  );
}
