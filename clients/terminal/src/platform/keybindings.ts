/**
 * KeybindingService — maps key chords to commands, gated by context-key when-clauses. Built on
 * `tinykeys` (tiny, `$mod` = ⌘ on mac / Ctrl elsewhere). Contributions `register({ key, command, when })`;
 * the service binds them to the window and, on a chord, evaluates `when` against the ContextKeyService
 * and `execute`s the command via the CommandService. This is one of the four things that separate a real
 * engine from a mock (keyboard-first, discoverable).
 */
import { tinykeys } from "tinykeys";
import { type IDisposable, toDisposable } from "./disposable";
import { type ServiceContainer, createServiceId, CommandServiceId, ContextKeyServiceId } from "./index";

export interface Keybinding {
  /** a tinykeys chord, e.g. "$mod+k", "$mod+b", "$mod+1", "$mod+Shift+p" */
  key: string;
  /** the CommandContribution id to execute */
  command: string;
  /** optional context-key when-clause gate */
  when?: string;
  /** optional args passed to the command */
  args?: string;
}

export interface KeybindingService {
  register(kb: Keybinding): IDisposable;
  attach(target: Window | HTMLElement): IDisposable;
  all(): Keybinding[];
}

export const KeybindingServiceId = createServiceId<KeybindingService>("keybinding");

export function createKeybindingService(container: ServiceContainer): KeybindingService {
  const bindings: Keybinding[] = [];
  let attached: Window | HTMLElement | null = null;
  let detach: (() => void) | null = null;

  const rebind = () => {
    detach?.();
    detach = null;
    if (!attached || bindings.length === 0) return;
    const map: Record<string, (e: KeyboardEvent) => void> = {};
    for (const kb of bindings) {
      // last registration wins for a given chord (mirrors VSCode keybinding precedence)
      map[kb.key] = (e: KeyboardEvent) => {
        const ctx = container.tryGet(ContextKeyServiceId);
        if (kb.when && ctx && !ctx.evaluate(kb.when)) return;
        e.preventDefault();
        void container.tryGet(CommandServiceId)?.execute(kb.command, kb.args);
      };
    }
    detach = tinykeys(attached as Window, map);
  };

  return {
    all: () => [...bindings],
    register(kb) {
      bindings.push(kb);
      rebind();
      return toDisposable(() => {
        const i = bindings.indexOf(kb);
        if (i >= 0) bindings.splice(i, 1);
        rebind();
      });
    },
    attach(target) {
      attached = target;
      rebind();
      return toDisposable(() => {
        detach?.();
        detach = null;
        attached = null;
      });
    },
  };
}
