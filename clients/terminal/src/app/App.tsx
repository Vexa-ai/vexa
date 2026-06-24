"use client";
/** The composition root — builds the DI container, wires surface commands, renders the workbench.
 *  Importing `../surfaces` registers every surface as a load-time side effect (before this body runs). */
import {
  createContainer, reg, ServicesProvider,
  CommandServiceId, createCommandService,
  ContextKeyServiceId, createContextKeyService,
} from "../platform";
import { LayoutServiceId, createLayoutService } from "../workbench/layout";
import { Workbench } from "../workbench/Workbench";
import { registry } from "../contributions";
import "../surfaces";

const container = createContainer([
  reg(ContextKeyServiceId, () => createContextKeyService()),
  reg(CommandServiceId, (c) => createCommandService(c)),
  reg(LayoutServiceId, () => createLayoutService("chat")),
]);
registry.commands().forEach((c) => container.get(CommandServiceId).register(c));

export function App() {
  return (
    <ServicesProvider container={container}>
      <Workbench />
    </ServicesProvider>
  );
}
