"use client";
/** Select (guidelines §4.4) — a trigger showing the current value and a chevron, opening a Menu of
 *  `menuitemradio` options. Keyboard and focus are the Menu's. */
import { ChevronDown } from "lucide-react";
import { Menu } from "./Menu";

export function Select<T extends string>({ label, value, options, onChange }: {
  label: string; value: T; options: { value: T; label: string }[]; onChange: (v: T) => void;
}) {
  const cur = options.find((o) => o.value === value);
  return (
    <Menu label={label} variant="chip" title={`${label}: ${cur?.label ?? ""}`}
      trigger={<><span className="vx-select-value">{cur?.label ?? label}</span><ChevronDown size={14} strokeWidth={1.75} aria-hidden /></>}
      items={options.map((o) => ({ key: o.value, label: o.label, checked: o.value === value, onSelect: () => onChange(o.value) }))} />
  );
}
