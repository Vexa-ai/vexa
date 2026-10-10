"use client";
/** Menu (guidelines §4.4) — a trigger button and the menu it opens, following the WAI-ARIA menu
 *  button pattern: Enter, Space or ArrowDown opens on the first item (ArrowUp on the last); the
 *  arrows, Home and End move; typing a letter jumps to the next item starting with it; Enter or
 *  Space activates; Escape closes and returns focus to the trigger; Tab closes and moves on; a
 *  click outside closes.
 *
 *  Items are data, not children, so the menu owns their roles, focus and spacing:
 *    `{ key, label, onSelect, icon?, hint?, checked?, danger?, disabled?, separatorBefore? }`.
 *  `checked` (true/false) makes it a `menuitemradio`; absent, a plain `menuitem`. Destructive items
 *  (`danger`) use the danger text colour and are expected below a separator. */
import { useCallback, useEffect, useId, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { Check } from "lucide-react";

export type MenuItem = {
  key: string;
  label: ReactNode;
  onSelect: () => void;
  icon?: ReactNode;
  hint?: ReactNode;
  checked?: boolean;
  danger?: boolean;
  disabled?: boolean;
  separatorBefore?: boolean;
  /** Extra attributes for tests and analytics-free hooks, e.g. `{ "data-ctx": "attach" }`. */
  data?: Record<string, string>;
};

export type MenuProps = {
  /** The trigger's accessible name. Required: icon-only triggers are common here. */
  label: string;
  /** What the trigger shows. Defaults to the label. */
  trigger?: ReactNode;
  items: MenuItem[];
  /** Shown instead of items when there are none. */
  empty?: ReactNode;
  /** Which edge of the trigger the menu aligns to, and which way it opens. */
  align?: "start" | "end";
  placement?: "bottom" | "top";
  /** Trigger look: `ghost` (icon/text button) or `chip`. */
  variant?: "ghost" | "chip";
  title?: string;
  /** Extra attributes on the trigger, e.g. `{ "data-composer-more": "" }`. */
  triggerData?: Record<string, string>;
  /** Content rendered at the foot of the menu, outside the item list (a short note). */
  footer?: ReactNode;
};

export function Menu({ label, trigger, items, empty, align = "start", placement = "bottom", variant = "ghost", title, triggerData, footer }: MenuProps) {
  const [open, setOpen] = useState(false);
  const btn = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const firstFocus = useRef<"first" | "last">("first");
  const id = useId();

  const itemsEls = () => Array.from(list.current?.querySelectorAll<HTMLElement>("[role^=menuitem]:not([aria-disabled=true])") ?? []);
  const close = useCallback((refocus: boolean) => { setOpen(false); if (refocus) btn.current?.focus(); }, []);

  useEffect(() => {
    if (!open) return;
    const els = itemsEls();
    const checked = list.current?.querySelector<HTMLElement>("[aria-checked=true]");
    (firstFocus.current === "last" ? els[els.length - 1] : checked ?? els[0])?.focus();
    const away = (e: PointerEvent) => {
      const t = e.target as Node;
      if (!list.current?.contains(t) && !btn.current?.contains(t)) close(false);
    };
    document.addEventListener("pointerdown", away, true);
    return () => document.removeEventListener("pointerdown", away, true);
  }, [open, close]);

  const onTriggerKey = (e: KeyboardEvent<HTMLButtonElement>) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      firstFocus.current = e.key === "ArrowUp" ? "last" : "first";
      setOpen(true);
    }
  };
  const onListKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const els = itemsEls();
    const at = els.indexOf(document.activeElement as HTMLElement);
    let to = -1;
    if (e.key === "ArrowDown") to = (at + 1) % els.length;
    else if (e.key === "ArrowUp") to = (at - 1 + els.length) % els.length;
    else if (e.key === "Home") to = 0;
    else if (e.key === "End") to = els.length - 1;
    else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(true); return; }
    else if (e.key === "Tab") { close(false); return; }
    else if (e.key.length === 1 && /\S/.test(e.key)) {
      const k = e.key.toLowerCase();
      for (let i = 1; i <= els.length; i++) {
        const cand = els[(at + i) % els.length];
        if ((cand.textContent ?? "").trim().toLowerCase().startsWith(k)) { to = (at + i) % els.length; break; }
      }
    }
    if (to < 0 || els.length === 0) return;
    e.preventDefault();
    els[to].focus();
  };

  return (
    <span className="vx-menu-anchor">
      <button ref={btn} type="button" className="vx-menu-trigger" data-variant={variant}
        aria-label={label} title={title ?? label} aria-haspopup="menu" aria-expanded={open} aria-controls={open ? id : undefined}
        onClick={() => { firstFocus.current = "first"; setOpen((v) => !v); }} onKeyDown={onTriggerKey} {...triggerData}>
        {trigger ?? label}
      </button>
      {open && (
        <div ref={list} id={id} role="menu" aria-label={label} className="vx-menu"
          data-align={align} data-placement={placement} onKeyDown={onListKey}>
          {items.length === 0 && empty ? <div className="vx-menu-empty">{empty}</div> : null}
          {items.map((it) => (
            <div key={it.key} style={{ display: "contents" }}>
              {it.separatorBefore && <div role="separator" className="vx-menu-sep" />}
              <button type="button" tabIndex={-1}
                role={it.checked === undefined ? "menuitem" : "menuitemradio"}
                aria-checked={it.checked === undefined ? undefined : it.checked}
                aria-disabled={it.disabled || undefined}
                className="vx-menu-item" data-danger={it.danger ? "" : undefined}
                onClick={() => { if (it.disabled) return; close(true); it.onSelect(); }}
                {...it.data}>
                {it.icon !== undefined || it.checked !== undefined
                  ? <span className="vx-menu-icon" aria-hidden>{it.checked ? <Check size={14} strokeWidth={1.75} /> : it.icon}</span> : null}
                <span className="vx-menu-label">{it.label}</span>
                {it.hint ? <span className="vx-menu-hint">{it.hint}</span> : null}
              </button>
            </div>
          ))}
          {footer ? <div className="vx-menu-foot">{footer}</div> : null}
        </div>
      )}
    </span>
  );
}
