/** KEYBOARD SHORTCUTS, ONE DEFINITION EACH (Vexa-ai/vexa#1829). A shortcut is a primary modifier
 *  (⌘ on a Mac, Ctrl elsewhere), an optional Shift and one key. The same record draws the hint a
 *  menu shows beside an item, writes the item's `aria-keyshortcuts`, and decides whether a key event
 *  is that shortcut — so the hint can never name a combo the handler does not answer.
 *
 *  Matching is on `KeyboardEvent.code` for letters (the physical key), so a Russian or German layout
 *  presses the same key as the hint says; named keys (Backspace) match on `key`. */

export type Shortcut = {
  /** a letter `"P"`, or a named key `"Backspace"` */
  key: string;
  shift?: boolean;
  /** an editing key: never taken out of a text field, even with the modifier held */
  editing?: boolean;
};

export function isMac(): boolean {
  if (typeof navigator === "undefined") return false;
  const nav = navigator as Navigator & { userAgentData?: { platform?: string } };
  const p = nav.userAgentData?.platform || navigator.platform || navigator.userAgent || "";
  return /mac|iphone|ipad|ipod/i.test(p);
}

const MAC_GLYPH: Record<string, string> = { Backspace: "⌫" };

/** "⇧⌘P" on a Mac (Apple's order: Shift before Command), "Ctrl+Shift+P" elsewhere. */
export function shortcutLabel(s: Shortcut, mac = isMac()): string {
  if (mac) return `${s.shift ? "⇧" : ""}⌘${MAC_GLYPH[s.key] ?? s.key}`;
  return ["Ctrl", ...(s.shift ? ["Shift"] : []), s.key].join("+");
}

/** The WAI-ARIA form: "Meta+Shift+P" / "Control+Shift+P". */
export function shortcutAria(s: Shortcut, mac = isMac()): string {
  return [mac ? "Meta" : "Control", ...(s.shift ? ["Shift"] : []), s.key].join("+");
}

export function matchesShortcut(e: KeyboardEvent, s: Shortcut, mac = isMac()): boolean {
  const mod = mac ? e.metaKey && !e.ctrlKey : e.ctrlKey && !e.metaKey;
  if (!mod || e.altKey || !!s.shift !== e.shiftKey) return false;
  return s.key.length === 1 ? e.code === `Key${s.key.toUpperCase()}` : e.key === s.key;
}

/** True while the person is typing: an input, a textarea, a select or anything contenteditable. */
export function isTypingTarget(t: EventTarget | null): boolean {
  const el = t as HTMLElement | null;
  if (!el || typeof el.tagName !== "string") return false;
  return el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable;
}

/** The chat list's shortcuts. Chosen so none shadows a Chrome or Safari default (macOS, Windows,
 *  Linux) or a macOS one, nor anything the composer uses (Enter, Shift+Enter, `/`, Escape, ⌘[ ⌘]).
 *  Firefox and Edge overlaps are listed in docs/docs/how-to/chat-controls.mdx. */
export const RAIL_SHORTCUTS = {
  pin: { key: "P", shift: true },
  hide: { key: "X", shift: true },
  delete: { key: "Backspace", editing: true },
  newChat: { key: "E", shift: true },
} as const satisfies Record<string, Shortcut>;
