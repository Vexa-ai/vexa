/** Theme — dark (default) vs day mode. The choice is persisted and applied as data-theme on <html>;
 *  globals.css repaints every surface from the swapped CSS variables. */
import { useEffect, useState } from "react";

export type Theme = "dark" | "light";
const KEY = "vexa.terminal.theme";

export function getTheme(): Theme {
  try { return localStorage.getItem(KEY) === "light" ? "light" : "dark"; } catch { return "dark"; }
}

export function applyTheme(t: Theme) {
  if (typeof document === "undefined") return;
  if (t === "light") document.documentElement.setAttribute("data-theme", "light");
  else document.documentElement.removeAttribute("data-theme");
  try { localStorage.setItem(KEY, t); } catch { /* storage unavailable */ }
}

/** Reactive theme state + a toggle, kept in sync with the DOM/localStorage. */
export function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>("dark");
  useEffect(() => { const t = getTheme(); setTheme(t); applyTheme(t); }, []);
  const toggle = () => setTheme((prev) => { const next = prev === "dark" ? "light" : "dark"; applyTheme(next); return next; });
  return [theme, toggle];
}
