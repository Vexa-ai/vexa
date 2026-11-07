"use client";
/** A catalogue frame: the same content in DARK and LIGHT side by side (each frame sets its own
 *  `data-theme`, which re-resolves every token in its subtree), optionally at fixed PANE widths
 *  (320 / 480 / 720) so container-query behaviour is visible at a glance. */
import type { ReactNode } from "react";

export function Frame({ children, widths }: { children: ReactNode; widths?: number[] }) {
  const one = (theme: "dark" | "light") => (
    <div data-theme={theme} className="vx-cat-frame">
      <div className="vx-cat-frame-label">{theme === "dark" ? "Dark" : "Light"}</div>
      {widths
        ? <div className="vx-cat-widths">{widths.map((w) => (
            <div key={w} className="vx-cat-pane vx-pane" style={{ width: w }}>
              <div className="vx-cat-frame-label">{w}px pane</div>
              {children}
            </div>
          ))}</div>
        : children}
    </div>
  );
  return <div className="vx-cat-pair">{one("dark")}{one("light")}</div>;
}
