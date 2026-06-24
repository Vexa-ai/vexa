/** term-ui-kit — shared primitives + icons painted from the prototype's dark tokens (globals.css). */
import type { CSSProperties } from "react";

const PATHS: Record<string, string> = {
  radio: "M16.2 7.8a6 6 0 0 1 0 8.4M19.1 4.9a10 10 0 0 1 0 14.2M7.8 16.2a6 6 0 0 1 0-8.4M4.9 19.1a10 10 0 0 1 0-14.2M12 10a2 2 0 1 0 0 4 2 2 0 0 0 0-4",
  msg: "M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z",
  panel: "M3 3h18v18H3zM9 3v18",
  mail: "M2 4h20v16H2zM22 7l-10 5L2 7",
  cal: "M3 4h18v18H3zM16 2v4M8 2v4M3 10h18",
  tasks: "M11 3 8 6 6.5 4.5M11 9 8 12l-1.5-1.5M11 15l-3 3-1.5-1.5M14 5h7M14 11h7M14 17h7",
  zap: "M13 2 3 14h9l-1 8 10-12h-9z",
  send: "M22 2 11 13M22 2l-7 20-4-9-9-4z",
  plus: "M5 12h14M12 5v14",
  x: "M18 6 6 18M6 6l12 12",
};

export function Icon({ name, size = 18, style }: { name: string; size?: number; style?: CSSProperties }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor"
      strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" style={{ flex: "none", ...style }}
      aria-hidden="true">
      <path d={PATHS[name] ?? ""} />
    </svg>
  );
}
